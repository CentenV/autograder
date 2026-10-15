from abc import ABC, abstractmethod

class TestHandler(ABC):

    @abstractmethod
    def before_script(self):
        raise NotImplementedError

    @abstractmethod
    def after_script(self):
        raise NotImplementedError
