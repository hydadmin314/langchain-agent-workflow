from abc import ABC, abstractmethod

class BaseAgent(ABC):
    @abstractmethod
    def build_agent(self):
        pass

    @abstractmethod
    def run(self, query: str) -> str:
        pass