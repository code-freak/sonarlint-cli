import json
import os
import socketserver
import socket
import select
import threading
import time
import errno
from threading import Thread


class LANGUAGES:
    """
    enum-like for all possible languages specified in the official documentation
    https://microsoft.github.io/language-server-protocol/specification#textdocumentitem
    """
    # ABAP
    abap = "abap"
    # Windows Bat
    bat = "bat"
    # BibTeX
    bibtex = "bibtex"
    # Clojure
    clojure = "clojure"
    # Coffeescript
    coffeescript = "coffeescript"
    # C
    c = "c"
    # C++
    cpp = "cpp"
    # C#
    csharp = "csharp"
    # CSS
    css = "css"
    # Diff
    diff = "diff"
    # Dart
    dart = "dart"
    # Dockerfile
    dockerfile = "dockerfile"
    # F#
    fsharp = "fsharp"
    # Git
    git = "git-commit"
    # Go
    go = "go"
    # Groovy
    groovy = "groovy"
    # Handlebars
    handlebars = "handlebars"
    # HTML
    html = "html"
    # Ini
    ini = "ini"
    # Java
    java = "java"
    # JavaScript
    javascript = "javascript"
    # JavaScript React
    javascriptreact = "javascriptreact"
    # JSON
    json = "json"
    # LaTeX
    latex = "latex"
    # Less
    less = "less"
    # Lua
    lua = "lua"
    # Makefile
    makefile = "makefile"
    # Markdown
    markdown = "markdown"
    # Objective-C
    objectivec = "objective-c"
    # Objective-C++
    objectivecpp = "objective-cpp"
    # Perl
    perl = "perl"
    # Perl 6
    perl6 = "perl6"
    # PHP
    php = "php"
    # Powershell
    powershell = "powershell"
    # Pug
    jade = "jade"
    # Python
    python = "python"
    # R
    r = "r"
    # Razor
    razor = "razor"
    # Ruby
    ruby = "ruby"
    # Rust
    rust = "rust"
    # SCSS
    scss = "scss"
    # SASS
    sass = "sass"
    # Scala
    scala = "scala"
    # ShaderLab
    shaderlab = "shaderlab"
    # Shell Script
    shellscript = "shellscript"
    # SQL
    sql = "sql"
    # Swift
    swift = "swift"
    # TypeScript
    typescript = "typescript"
    # TypeScript React
    typescriptreact = "typescriptreact"
    # TeX
    tex = "tex"
    # Visual Basic
    vb = "vb"
    # XML
    xml = "xml"
    # XSL
    xsl = "xsl"
    # YAML
    yaml = "yaml"

    # not in the official docs currently but supported by Sonar at least
    kotlin = "kotlin"

    def __init__(self):
        raise RuntimeError("Leave me alone")


FILE_EXTENSIONS = {
    LANGUAGES.html: ['htm', 'html'],
    LANGUAGES.javascript: ['js'],
    LANGUAGES.php: ['php'],
    LANGUAGES.python: ['py'],
    LANGUAGES.typescript: ['ts'],
    LANGUAGES.kotlin: ['kt'],
    LANGUAGES.java: ['java']
}

FILE_EXTENSIONS_REVERSE = {}
for k, v in FILE_EXTENSIONS.items():
    for vv in v:
        FILE_EXTENSIONS_REVERSE[vv] = k


def get_language_id(path):
    _, ext = os.path.splitext(path)
    ext = ext[1:]
    if ext in FILE_EXTENSIONS_REVERSE:
        return FILE_EXTENSIONS_REVERSE[ext]
    return None

def urify(path):
    if type(path) is list:
        return [urify(the_path) for the_path in path]
    return "file://%s" % path


def unurify(path):
    if type(path) is list:
        return [path(the_path) for the_path in path]
    if path.startswith('file://'):
        return path[7:]
    return path


class JsonRPCMessage:
    def json(self) -> dict:
        msg = {
            "jsonrpc": "2.0"
        }
        return msg

    def __str__(self):
        return json.dumps(self.json(), separators=(',', ':'))


class LanguageServerNotification(JsonRPCMessage):
    def __init__(self, method: str, params: any = None):
        self.method = method
        self.params = params

    def json(self) -> dict:
        the_json = super().json()
        the_json.update({
            "method": self.method,
            "params": self.params
        })
        return the_json

    def __str__(self):
        body = super().__str__()
        header = "Content-Length: %s\r\n" % len(body.encode("utf-8"))
        return "%s\r\n%s" % (header, body)


class LanguageServerRequest(LanguageServerNotification):
    id_count = 0

    def __init__(self, method: str, params: any = None):
        super().__init__(method, params)
        self.id = LanguageServerRequest.id_count
        LanguageServerRequest.id_count += 1

    def json(self) -> dict:
        the_json = super().json()
        the_json['id'] = self.id
        return the_json


def parse_header_into_dict(header: str) -> dict:
    lines = header.strip("\r\n").split("\r\n")
    ret = {}
    for line in lines:
        if ":" not in line:
            print("Invalid header-line '%s'..." % line)
            continue
        key, val = line.split(":", 1)
        ret[key.rstrip(" ")] = val.lstrip(" ")
    return ret


class BaseServer:
    """
    A basic language server that has a single socket connection to a language server and can receive and send JSON-RPC
    messages
    """

    def __init__(self, on_msg: callable = None, on_connection: callable = None):
        self._response_queue = {}
        self._event_listeners = {}
        self._on_connection = on_connection
        self._on_msg = on_msg
        self._stop = threading.Event()
        self._connection: socket.socket = None
        self._poll_interval = .5
        self._last_select = None
        self._buffer = bytearray()
        self._buffer_max_size = 1024 * 1024 * 5 # 5MiB
        self._body_size = -1
        self._buffer_has_data = threading.Event()
        self._send_queue = []
        self._send_queue_access = threading.Lock()
        self._send_queue_has_data = threading.Event()

    @property
    def socket(self):
        if self._connection is None:
            raise RuntimeError("No connection has been established, yet")
        return self._connection

    def handle_socket(self, socket, addr, _):
        """
        Handle an established connection to a language server
        If more than one connection has been made the connection will be closed

        :param socket: The socket to the language server
        :param addr: The remote address of the language server
        :param _:
        :return:
        """
        sock: socket.socket = socket
        ip, port = addr

        if self._connection is not None:
            print("More than one connection to client. Dropping connection...")
            # do not allow more than one connection
            sock.close()
            return

        self._connection = sock
        sock.setblocking(False)
        # Run the wait poll in a separate thread to be really non-blocking
        listen_thread = Thread(target=self._wait_for_data)
        listen_thread.start()
        self._on_connection(self, sock)
        while not self._stop.isSet():
            self._buffer_has_data.wait(self._poll_interval)
            while self._read_json_rpc_msg():
                pass
            self._buffer_has_data.clear()

    def send_request(self, method, params, cb):
        """
        Send a RPC request and expect a response
        The callback cb will be called with the response once it arrives

        :param method:
        :param params:
        :param cb:
        :return:
        """
        rpc = LanguageServerRequest(method, params)
        self._response_queue[rpc.id] = cb
        with self._send_queue_access:
            self._send_queue.append(str(rpc).encode())
        self._send_queue_has_data.set()

    def send_notification(self, method, params):
        """
        Send a message to the server without expecting any response (notification)

        :param method:
        :param params:
        :return:
        """
        rpc = LanguageServerNotification(method, params)
        with self._send_queue_access:
            self._send_queue.append(str(rpc).encode())
        self._send_queue_has_data.set()

    def _wait_for_data(self):
        """
        Runs in another thread (called by handle_socket) and waits for data on the socket connection
        If there is data to receive it will be flushed into self._buffer and parsed by read_json_rpc_msg on the main thread
        see _drain_socket and _read_json_rpc_msg

        :return:
        """
        sock = self._connection
        while not self._stop.isSet() and sock.fileno() >= 0:
            selected = select.select([sock], [sock], [], self._poll_interval)
            now = time.time()
            if self._last_select is not None:
                if (self._last_select + 10 * self._poll_interval) <= now:
                    print("Last select is more than 10x poll intervall ago, possible thread locking detected")
            self._last_select = now
            try:
                if selected[0] and not self._buffer_has_data.isSet():
                    # Reading the buffer is done on this thread but publishing the results will happen on the main
                    # thread so we do not block the receiving thread
                    self._drain_socket()
                    self._buffer_has_data.set()
                if selected[1] and self._send_queue_has_data.isSet():
                    with self._send_queue_access:
                        while len(self._send_queue) > 0:
                            self._connection.sendall(self._send_queue.pop(0))
                    self._send_queue_has_data.clear()
            except IOError as e:
                if e.errno == errno.EWOULDBLOCK:
                    pass
                else:
                    raise e

    def _drain_socket(self):
        """
        Also runs on the receiving thread and simply reads all data from the socket into _buffer until buffer exceeds
        max_buffer_size or connection holds no more data

        :return:
        """
        size = 4096
        while True:
            data = self._connection.recv(size)
            self._buffer.extend(data)
            if len(data) < size or len(self._buffer) > self._buffer_max_size:
                break

    def _read_json_rpc_msg(self):
        """
        Try to read a JSON-RPC header and body from the buffer. If the header has already arrived but not the body
        the function will parse the header and wait until the buffer contains enough bytes for the body.
        This is all happening on the main thread! We dont want to block the receiving thread

        :return:
        """
        # Parse a new header if we are not waiting for a body to arrive fully
        if self._body_size == -1:
            # look for \r\n\r\n in buffer
            header_end = self._buffer.find(b"\r\n\r\n")
            if header_end == -1:
                return False
            header = self._buffer[:header_end].decode('utf-8')
            self._buffer = self._buffer[header_end + 4:]
            header_dict = parse_header_into_dict(header)
            if 'Content-Length' not in header_dict:
                print("Invalid LanguageServer message: no Content-Length header")
                return False
            self._body_size = int(header_dict['Content-Length'])

        # check if we have enough data for our body
        if len(self._buffer) < self._body_size:
            return False

        # take the body from the buffer and publish the RPC message
        body = self._buffer[:self._body_size]
        self._buffer = self._buffer[self._body_size:]
        self._body_size = -1
        self.publish_rpc_msg(body)
        return True

    def publish_rpc_msg(self, body: bytes):
        """
        Check if we have anyone waiting for the given RPC message (if it is a response) or otherwise simply

        :param body:
        :return:
        """
        json_msg = json.loads(body.decode('utf-8'))
        if "id" in json_msg:
            # check if we are waiting for this response and call the corresponsing callback function
            if json_msg["id"] in self._response_queue:
                cb = self._response_queue[json_msg["id"]]
                del self._response_queue[json_msg["id"]]
                cb(json_msg["result"])
            else:
                # this should never happen but who knows...
                print("Got response for message #%s we never sent..." % json_msg["id"])
        else:
            # event sent from the server
            # check if we have any event listeners for it and call them
            if json_msg['method'] in self._event_listeners:
                for listener in self._event_listeners[json_msg['method']]:
                    listener(json_msg['params'])
        # call the generic listener last in all cases
        if self._on_msg is not None:
            self._on_msg(json_msg)

    def on(self, msg_type: str, cb: callable):
        """
        Register a listener for notification messages from the server

        :param msg_type:
        :param cb:
        :return:
        """
        if msg_type not in self._event_listeners:
            self._event_listeners[msg_type] = []
        if cb not in self._event_listeners[msg_type]:
            self._event_listeners[msg_type].append(cb)


class ReverseServer(BaseServer):
    """
    A client-server connection where the client starts a TCP server and the language-server connects
    to the client's TCP server.
    """

    def __init__(self, on_msg: callable = None, on_connection: callable = None, ip: str = "localhost"):
        super().__init__(on_msg, on_connection)
        self.server = socketserver.TCPServer((ip, 0), self.handle_socket)

    @property
    def addr(self):
        return self.server.server_address

    def start(self):
        try:
            self.server.serve_forever()
        except KeyboardInterrupt:
            pass
        if len(self._response_queue) > 0:
            print("Warning: There are %s RPC calls without answer left in queue" % len(self._response_queue))

    def stop(self):
        self._stop.set()
        self._stop.wait()
        self.server.shutdown()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.stop()
