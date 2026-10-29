"""Decision Studio comparisons and deterministic decision maps."""

from typing import Literal

from pydantic import Field

from app.schemas import Assumption, CalcInput, Calculation, Model

OptionId = Literal["fix_now", "isolate_fix", "wait_6h", "wait_24h", "wait_72h"]


class DecisionOption(Model):
    id: OptionId
    name: str
    delay_hours: float
    current_cost: float = Field(description="Total at current assumptions, summing the displayed cost lines in EGP.")
    expected_cost: float = Field(description="Mean total across shared sampled price scenarios, in EGP.")
    p10: float
    p50: float
    p90: float = Field(description="Sampled p90 worst case in EGP, not a maximum.")
    expected_regret: float = Field(ge=0)
    win_share: float = Field(ge=0, le=1, description="Fraction X: cheapest in X% of sampled price scenarios; ties count for each cheapest option.")
    win_share_text: str
    cost_lines: list[Calculation]
    net_benefit_vs_fix_now: float = Field(description="Expected option cost minus expected fix_now cost in EGP; negative means the option costs less.")


class BreakEven(Model):
    option_id: OptionId
    parameter: Literal["leak_m3h", "delay_per_hour"]
    value: float | None = None
    unit: str
    formula: str
    inputs: list[CalcInput]
    reason: str | None = None


class DecisionMap(Model):
    x_key: str
    x_name: str
    x_unit: str
    x_values: list[float]
    y_key: str
    y_name: str
    y_unit: str
    y_values: list[float]
    cells: list[list[OptionId | None]]
    current_x: float
    current_y: float
    current_option_id: OptionId | None = Field(description="Cheapest option at current assumption values; null on an exact tie.")


class DecisionReport(Model):
    incident_id: str
    currency: Literal["EGP"] = "EGP"
    recommended_option_id: OptionId | None = Field(description="Least expected sampled cost; null on an exact tie.")
    runs: int
    seed: int
    assumptions: list[Assumption]
    options: list[DecisionOption]
    break_even: list[BreakEven]
    decision_map: DecisionMap
    review: bool = False
    review_reason: str | None = None

    def dump(self) -> dict:
        data = super().dump()
        data["recommendedOptionId"] = self.recommended_option_id
        data["decisionMap"]["currentOptionId"] = self.decision_map.current_option_id
        return data
