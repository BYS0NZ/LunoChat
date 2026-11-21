import socket

s = socket.socket()
print("Socket successfully created")

port = 6767
s.connect(('127.0.0.1', port))

message = s.recv(1024)
print(message.decode())

s.close()