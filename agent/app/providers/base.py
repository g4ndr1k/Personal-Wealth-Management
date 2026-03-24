from abc import ABC, abstractmethod
from app.schemas import Classification


class Provider(ABC):
    name: str

    @abstractmethod
    def classify(self, message: dict) -> Classification:
        raise NotImplementedError
