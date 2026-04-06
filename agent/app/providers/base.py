from abc import ABC, abstractmethod
from app.schemas import Classification


class Provider(ABC):
    name: str

    @abstractmethod
    def classify(self, message: dict) -> Classification:
        raise NotImplementedError

    def close(self) -> None:
        """Release any provider resources."""
        return None
