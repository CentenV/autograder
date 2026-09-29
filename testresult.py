from dataclasses import dataclass, asdict
import json

@dataclass
class TestResult:
    name: str
    score: float
    max_score: float
    output: str

    def to_dict(self):
        return asdict(self)

    def to_json(self):
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data):
        return cls(**data)

    @classmethod
    def from_json(cls, data):
        return cls.from_dict(json.loads(data))
