"""曝气控制业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

import math
from typing import Any

from app.store import store

MODULE = "aeration"
REQUIRED_FIELDS = ["记录编号", "曝气池编号", "溶解氧值"]
STATUS_ORDER = ["待调节", "已调节", "待复核", "已锁定"]
ACTION_RULES = {"提交调节": "已调节", "复核确认": "待复核", "锁定参数": "已锁定"}
NEGATIVE_ACTIONS = []

# 溶解氧-风量联动建议的池级口径：按曝气池编号给出溶解氧目标范围、风量调节阈值与风机频率上限。
# 未配置的池号走 DEFAULT_TANK_RULE，保证新池号也有一份保守口径可用。
TANK_RULES: dict[str, dict[str, float]] = {
    "AERA-0001": {"do_low": 1.8, "do_high": 2.5, "airflow_step": 5.0, "freq_limit": 50.0},
    "AERA-0002": {"do_low": 1.5, "do_high": 2.2, "airflow_step": 4.0, "freq_limit": 50.0},
    "AERA-0003": {"do_low": 2.0, "do_high": 3.0, "airflow_step": 6.0, "freq_limit": 45.0},
}
DEFAULT_TANK_RULE = {"do_low": 1.5, "do_high": 3.0, "airflow_step": 5.0, "freq_limit": 50.0}

# 溶解氧偏离目标范围每 0.5 mg/L 计一档、最多三档；优先级按偏离量划段。
GEAR_STEP_DO = 0.5
GEAR_MAX = 3
PRIORITY_BANDS = ((1.0, "高"), (0.5, "中"), (0.0, "低"))
SUGGESTION_FIELDS = ["溶解氧值", "风量设定", "风机频率"]


def _to_float(value: Any) -> float | None:
    """把输入转成浮点数；空值、非数值或非有限值都返回 None，由调用方决定怎么解释。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


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

    def tank_rules(self) -> list[dict[str, Any]]:
        """按曝气池编号展开联动建议口径：溶解氧目标范围、风量调节阈值与风机频率上限。"""
        rules = [
            {
                "曝气池编号": tank,
                "溶解氧下限": rule["do_low"],
                "溶解氧上限": rule["do_high"],
                "风量调节阈值": rule["airflow_step"],
                "风机频率上限": rule["freq_limit"],
            }
            for tank, rule in sorted(TANK_RULES.items())
        ]
        rules.append({
            "曝气池编号": "未配置池号（默认）",
            "溶解氧下限": DEFAULT_TANK_RULE["do_low"],
            "溶解氧上限": DEFAULT_TANK_RULE["do_high"],
            "风量调节阈值": DEFAULT_TANK_RULE["airflow_step"],
            "风机频率上限": DEFAULT_TANK_RULE["freq_limit"],
        })
        return rules

    def build_suggestion(self, entry_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """生成溶解氧-风量联动建议并写回记录，刷新后建议与记录状态保持一致。

        三类情况不出建议、只说明原因：溶解氧为空、风机频率超出上限、同一记录按相同参数重复提交。
        """
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"曝气记录 {entry_id} 不存在或已归档"
        # 提交里带了的字段以提交为准（哪怕置空），没带的才回落到记录原值；
        # 这样「溶解氧为空」能被明确拦下并说明原因，而不是悄悄沿用旧值。
        merged = {
            field: values[field] if field in values else entry.get(field)
            for field in SUGGESTION_FIELDS
        }
        do_raw = merged.get("溶解氧值")
        if not str(do_raw or "").strip():
            return None, f"曝气记录 {entry_id} 的溶解氧值为空，无法生成联动建议，请先补录溶解氧数据"
        do_value = _to_float(do_raw)
        if do_value is None:
            return None, f"曝气记录 {entry_id} 的溶解氧值「{do_raw}」不是有效数值，无法对照目标范围给建议"
        rule = TANK_RULES.get(str(entry.get("曝气池编号") or "").strip(), DEFAULT_TANK_RULE)
        freq_value = _to_float(merged.get("风机频率"))
        if freq_value is not None and freq_value > rule["freq_limit"]:
            return None, (
                f"曝气记录 {entry_id} 的风机频率 {freq_value:g} Hz 超出上限 {rule['freq_limit']:g} Hz，"
                "联动建议已挂起，请先把频率降回上限以内"
            )
        inputs = {field: str(merged.get(field) or "").strip() for field in SUGGESTION_FIELDS}
        existing = entry.get("联动建议")
        if isinstance(existing, dict) and entry.get("联动建议输入") == inputs:
            return None, (
                f"曝气记录 {entry_id} 已按相同溶解氧与风量生成过联动建议"
                f"（{existing.get('建议档位', '—')}，优先级{existing.get('优先级', '—')}），无需重复提交"
            )
        suggestion = self._linkage_suggestion(rule, do_value, _to_float(merged.get("风量设定")))
        for field in SUGGESTION_FIELDS:
            entry[field] = merged.get(field)
        entry["联动建议"] = suggestion
        entry["联动建议输入"] = inputs
        return entry, f"曝气记录 {entry_id} 联动建议已生成：{suggestion['建议档位']}（优先级{suggestion['优先级']}）"

    @staticmethod
    def _linkage_suggestion(rule: dict[str, float], do_value: float, airflow: float | None) -> dict[str, Any]:
        """按池级口径把溶解氧偏离量换算成建议档位与优先级。"""
        do_low, do_high, step = rule["do_low"], rule["do_high"], rule["airflow_step"]
        target_range = f"{do_low:g}–{do_high:g} mg/L"
        if do_low <= do_value <= do_high:
            return {
                "建议档位": "维持当前档位",
                "优先级": "低",
                "建议说明": f"溶解氧 {do_value:g} mg/L 处于目标范围 {target_range} 内，风量维持当前设定",
                "目标范围": target_range,
                "风量调节阈值": step,
            }
        if do_value < do_low:
            deviation, direction = do_low - do_value, "升"
        else:
            deviation, direction = do_value - do_high, "降"
        gears = min(GEAR_MAX, max(1, math.ceil(deviation / GEAR_STEP_DO)))
        priority = next(label for bound, label in PRIORITY_BANDS if deviation >= bound)
        if airflow is not None:
            target = airflow + gears * step if direction == "升" else max(0.0, airflow - gears * step)
            note = (
                f"溶解氧 {do_value:g} mg/L 偏离目标范围 {target_range} {deviation:.2f} mg/L，"
                f"建议风量由 {airflow:g} 调至 {target:g}（每档 {step:g}）"
            )
        else:
            note = (
                f"溶解氧 {do_value:g} mg/L 偏离目标范围 {target_range} {deviation:.2f} mg/L，"
                f"建议按每档 {step:g} {direction}风量 {gears} 档；风量设定不是数值，未换算目标风量"
            )
        return {
            "建议档位": f"{direction} {gears} 档",
            "优先级": priority,
            "建议说明": note,
            "目标范围": target_range,
            "风量调节阈值": step,
        }

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
