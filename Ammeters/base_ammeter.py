import os
import socket
from abc import ABC, abstractmethod

NotImplementedErrorMsg = "Subclasses must implement this property."

class AmmeterEmulatorBase(ABC):
    def __init__(self, port: int):
        self.port = port

    def start_server(self) -> None:
        """
        Starts the server to listen for client requests.
        The server will run indefinitely, handling one client request at a time.
        """
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if os.name == "posix":  # restart while old connections are in TIME_WAIT; unsafe on Windows
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(('localhost', self.port))
            s.listen()
            print(f"{self.__class__.__name__} is running on port {self.port}")
            while True:
                conn, addr = s.accept()
                with conn:
                    print(f"Connected by {addr}")
                    try:
                        data = conn.recv(1024)
                        if data == self.get_current_command:
                            # Call the specific measure_current() method defined in subclasses
                            current = self.measure_current()
                            conn.sendall(str(current).encode('utf-8'))
                    except Exception as exc:  # a vanished client, or a bad reading, must not end the loop
                        print(f"Dropped connection from {addr}: {exc}")

    @property
    @abstractmethod
    def get_current_command(self) -> bytes:
        """
        This property must be implemented by each subclass to provide the specific
        command to get the current measurement.
        """
        raise NotImplementedError(NotImplementedErrorMsg)

    @abstractmethod
    def measure_current(self) -> float:
        """
        This method must be implemented by each subclass to provide the specific
        logic for current measurement.
        """
        raise NotImplementedError(NotImplementedErrorMsg)

