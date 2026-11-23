from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator


class Role(StrEnum):
    operator = 'operator'
    supervisor = 'supervisor'
    viewer = 'viewer'


class Grade(StrEnum):
    A = 'A'
    B = 'B'
    C = 'C'


class Machine(StrEnum):
    VS01 = 'VS-01'
    VS02 = 'VS-02'
    VS03 = 'VS-03'


Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Note = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=300)]
Code = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')]
Qty = Annotated[int, Field(strict=True, ge=1, le=2_000_000)]
Percent = Annotated[int, Field(strict=True, ge=0, le=100)]


class Payload(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class Login(Payload):
    username: Text
    password: Annotated[str, Field(min_length=1, max_length=160)]


class LotInput(Payload):
    code: Code
    product: Text
    machine: Machine
    units: Qty
    film_sku: Code


class MeasureInput(Payload):
    sample: Code
    machine: Machine
    wrinkle_pct: Annotated[float, Field(ge=0, le=100)] | None = None
    offset_mm: Annotated[float, Field(ge=0, le=20)] | None = None
    delta_e: Annotated[float, Field(ge=0, le=60)] | None = None
    seal_gap_mm: Annotated[float, Field(ge=0, le=12)] | None = None
    gloss_cv: Annotated[float, Field(ge=0, le=100)] | None = None


class DefectKind(StrEnum):
    wrinkle = 'wrinkle'
    offset = 'offset'
    color = 'color'
    seal = 'seal'
    gloss = 'gloss'


class Zone(StrEnum):
    front = 'front'
    back = 'back'
    top_seal = 'top-seal'
    bottom_seal = 'bottom-seal'


class MarkInput(Payload):
    kind: DefectKind
    zone: Zone
    x_pct: Percent
    y_pct: Percent
    note: Note


class MarkVerdict(StrEnum):
    confirmed = 'confirmed'
    dismissed = 'dismissed'


class MarkReview(Payload):
    state: MarkVerdict
    note: Note


class GradeReview(Payload):
    grade: Grade
    reason: Note | None = None
    rework_units: Qty | None = None


class Verification(MeasureInput):
    replaced_units: Qty
    note: Note


CHANNELS = ('wrinkle_pct', 'offset_mm', 'delta_e', 'seal_gap_mm', 'gloss_cv')
VERIFICATION_LIMITS = dict(zip(CHANNELS, (1.0, 0.5, 2.0, 0.35, 4.0), strict=True))

CHANNEL_BOUNDS = {'wrinkle_pct': (0, 100), 'offset_mm': (0, 20), 'delta_e': (0, 60),
                  'seal_gap_mm': (0, 12), 'gloss_cv': (0, 100)}


class ChannelCorrection(Payload):
    """单个通道的修正量；按通道绝对量叠加到原读数上。"""

    channel: str
    delta: Annotated[float, Field(ge=-10_000, le=10_000, allow_inf_nan=False)]

    @field_validator('channel')
    @classmethod
    def known_channel(cls, value):
        if value not in CHANNELS:
            raise ValueError('未知的测量通道')
        return value


class CalibrationInput(Payload):
    machine: Machine
    window_start: datetime
    window_end: datetime
    corrections: Annotated[list[ChannelCorrection], Field(min_length=0, max_length=len(CHANNELS))]
    note: Note

    @field_validator('corrections')
    @classmethod
    def unique_channels(cls, value):
        channels = [item.channel for item in value]
        if len(channels) != len(set(channels)):
            raise ValueError('同一通道只能登记一次修正量')
        return value

    @field_validator('window_end')
    @classmethod
    def ordered_window(cls, value, info):
        start = info.data.get('window_start')
        if start is not None and value <= start:
            raise ValueError('受影响时段的结束必须晚于开始')
        return value


class CalibrationAmend(Payload):
    corrections: Annotated[list[ChannelCorrection], Field(min_length=0, max_length=len(CHANNELS))]
    note: Note

    @field_validator('corrections')
    @classmethod
    def unique_channels(cls, value):
        channels = [item.channel for item in value]
        if len(channels) != len(set(channels)):
            raise ValueError('同一通道只能登记一次修正量')
        return value


class TaskClear(Payload):
    note: Note
