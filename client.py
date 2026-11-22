import socket
import threading

nickname = input("Choose your nickname: ")
client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

port = 59000
client.connect(('127.0.0.1', port))

def client_receive():
    while True:
        try:
            message = client.recv(1024).decode('utf-8')
            if message == 'nickname: ':
                client.send(nickname.encode('utf-8'))
            else:
                print(message)
        except:
            print("An error occurred!")
            client.close()
            break

def client_send():
    while True:
        message = f'{nickname}: {input("")}'
        client.send(message.encode('utf-8'))

# Start the receiving thread
receive_thread = threading.Thread(target=client_receive)
receive_thread.start()

# Start the sending thread
send_thread = threading.Thread(target=client_send)
send_thread.start()