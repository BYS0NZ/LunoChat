import socket
import sys
import threading
from cryptography.exceptions import InvalidTag
from crypto import SecureChannel
from framing import send_message, recv_message

lock = threading.Lock()
connection = None                 # the one active chat connection
initiator = False                 # True if WE dialed out, False if they dialed us
connected = threading.Event()     # set once a connection exists
closed = threading.Event()        # set when the chat is over


def set_connection(conn, is_initiator):
    """Keep the first connection that arrives, reject any others."""
    global connection, initiator
    with lock:
        if connection is None:
            connection = conn
            initiator = is_initiator
            connected.set()
            return True
    conn.close()
    return False


def listen(port):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", port))
    server.listen()
    print(f"Listening on port {port}...")
    while True:
        try:
            conn, addr = server.accept()
        except OSError:
            break
        if set_connection(conn, False):     # they dialed us
            print(f"Peer {addr} connected.")


def connect(host, port):
    conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        conn.connect((host, port))
    except OSError as e:
        print(f"Could not connect: {e}")
        closed.set()
        connected.set()   # wake up the main thread so it can exit
        return
    if set_connection(conn, True):      # we dialed out
        print(f"Connected to {host}:{port}")


def receive_loop(channel):
    while True:
        try:
            data = channel.recv()
        except (ConnectionError, OSError, ValueError, InvalidTag):
            break
        print(f"\rPeer: {data.decode('utf-8', errors='replace')}")
    print("Peer disconnected. Press Enter to quit.")
    closed.set()


def main():
    if len(sys.argv) < 2:
        print("Usage: python peer.py <my_port> [host:port]")
        return

    my_port = int(sys.argv[1])

    threading.Thread(target=listen, args=(my_port,), daemon=True).start()

    if len(sys.argv) >= 3:
        host, port = sys.argv[2].split(":")
        threading.Thread(target=connect, args=(host, int(port)), daemon=True).start()

    connected.wait()              # block until someone connects (either direction)
    if closed.is_set():
        return

    # Handshake: swap keys and set up encryption
    try:
        channel = SecureChannel(connection, initiator)
    except (ConnectionError, OSError, ValueError) as e:
        print(f"Handshake failed: {e}")
        connection.close()
        return
    print("Secure channel established.")

    threading.Thread(target=receive_loop, args=(channel,), daemon=True).start()

    print("You can type now. Ctrl+C to quit.")
    try:
        while not closed.is_set():
            text = input()
            if closed.is_set():
                break
            channel.send(text.encode("utf-8"))
    except (KeyboardInterrupt, EOFError, OSError):
        pass
    finally:
        connection.close()


if __name__ == "__main__":
    main()