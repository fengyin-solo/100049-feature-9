"""曝气控制业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "aeration"
REQUIRED_FIELDS = ["记录编号", "曝气池编号", "溶解氧值"]
STATUS_ORDER = ["待调节", "已调节", "待复核", "已锁定"]
ACTION_RULES = {"提交调节": "已调节", "复核确认": "待复核", "锁定参数": "已锁定"}
NEGATIVE_ACTIONS = []

# 溶解氧与风量联动规则：按曝气池编号维护目标范围、调节阈值与风机频率上限。
POOL_RULES: dict[str, dict[str, float]] = {
    "1号曝气池": {"溶解氧下限": 1.5, "溶解氧上限": 2.5, "风量调节阈值": 0.5, "风机频率上限": 50.0},
    "2号曝气池": {"溶解氧下限": 1.8, "溶解氧上限": 3.0, "风量调节阈值": 0.6, "风机频率上限": 50.0},
    "3号曝气池": {"溶解氧下限": 2.0, "溶解氧上限": 3.5, "风量调节阈值": 0.8, "风机频率上限": 50.0},
}


def _parse_number(value: Any) -> float | None:
    """把记录里的数值字段解析成 float；空值或非数值都按 None 处理。"""
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


class AerationService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("记录编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"曝气记录 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于曝气控制可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"曝气记录已{action}"

    def generate_suggestion(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        """生成溶解氧与风量设定的联动建议，建议写回记录，刷新后仍与记录状态一致。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"曝气记录 {entry_id} 不存在或已归档"
        if entry.get("联动建议"):
            return None, (
                f"曝气记录 {entry.get('记录编号', entry_id)} 已生成联动建议，"
                "同一记录请勿重复提交"
            )
        do_value = _parse_number(entry.get("溶解氧值"))
        if do_value is None:
            return None, "溶解氧值为空或不是有效数值，无法判定是否超限，请先补录溶解氧值"
        pool = str(entry.get("曝气池编号") or "").strip()
        rule = POOL_RULES.get(pool)
        if rule is None:
            return None, f"曝气池 {pool or '（空）'} 未配置溶解氧与风量联动规则，无法生成建议"
        freq = _parse_number(entry.get("风机频率"))
        if freq is not None and freq > rule["风机频率上限"]:
            return None, (
                f"风机频率 {freq:g} Hz 超出上限 {rule['风机频率上限']:g} Hz，"
                "请先核查风机工况后再生成联动建议"
            )

        low = rule["溶解氧下限"]
        high = rule["溶解氧上限"]
        threshold = rule["风量调节阈值"]
        if do_value < low:
            deviation = low - do_value
            if deviation > threshold:
                gear, priority = "风量上调两档", "高"
            else:
                gear, priority = "风量上调一档", "中"
            situation = f"溶解氧 {do_value:g} mg/L 低于目标下限 {low:g} mg/L"
        elif do_value > high:
            deviation = do_value - high
            if deviation > threshold:
                gear, priority = "风量下调两档", "高"
            else:
                gear, priority = "风量下调一档", "中"
            situation = f"溶解氧 {do_value:g} mg/L 高于目标上限 {high:g} mg/L"
        else:
            gear, priority = "维持当前风量设定", "低"
            situation = f"溶解氧 {do_value:g} mg/L 处于目标范围 {low:g}~{high:g} mg/L 内"

        advice = (
            f"{pool} 溶解氧目标 {low:g}~{high:g} mg/L、风量调节阈值 {threshold:g} mg/L；"
            f"{situation}，建议{gear}（优先级：{priority}）"
        )
        entry["联动建议"] = gear
        entry["建议优先级"] = priority
        entry["联动建议说明"] = advice
        return entry, f"联动建议已生成：{advice}"
