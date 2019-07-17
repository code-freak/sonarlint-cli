import os
import subprocess
import threading

from sonarlintcli.languageserver import urify, unurify, LANGUAGES, get_language_id

JAR_DOWNLOAD_LANGUAGE_SERVER = "https://repox.jfrog.io/repox/sonarsource/org/sonarsource/sonarlint/core/sonarlint-language-server/4.3.1.2486/sonarlint-language-server-4.3.1.2486.jar"
JAR_DOWNLOAD_LANGUAGES = {
    LANGUAGES.html: "https://repox.jfrog.io/repox/sonarsource/org/sonarsource/html/sonar-html-plugin/3.1.0.1615/sonar-html-plugin-3.1.0.1615.jar",
    LANGUAGES.javascript: "https://repox.jfrog.io/repox/sonarsource/org/sonarsource/javascript/sonar-javascript-plugin/5.1.1.7506/sonar-javascript-plugin-5.1.1.7506.jar",
    LANGUAGES.php: "https://repox.jfrog.io/repox/sonarsource/org/sonarsource/php/sonar-php-plugin/3.0.0.4537/sonar-php-plugin-3.0.0.4537.jar",
    LANGUAGES.python: "https://repox.jfrog.io/repox/sonarsource/org/sonarsource/python/sonar-python-plugin/1.12.0.2726/sonar-python-plugin-1.12.0.2726.jar",
    LANGUAGES.typescript: "https://repox.jfrog.io/repox/sonarsource/org/sonarsource/typescript/sonar-typescript-plugin/1.9.0.3766/sonar-typescript-plugin-1.9.0.3766.jar",
    LANGUAGES.kotlin: "https://repox.jfrog.io/repox/sonarsource/org/sonarsource/slang/sonar-kotlin-plugin/1.6.0.626/sonar-kotlin-plugin-1.6.0.626.jar",
    LANGUAGES.java: "https://repox.jfrog.io/repox/sonarsource/org/sonarsource/java/sonar-java-plugin/5.9.2.16552/sonar-java-plugin-5.9.2.16552.jar"
}


def ensure_callable(val):
    if callable(val):
        return val
    return lambda *args, **kwargs: None


class SonarLintRuleResolver:
    def __init__(self, language_server):
        self._language_server = language_server
        self._diagnostics_cache = {}
        self._resolve_queue = {}

    def get_by_diagnostics(self, file, diagnostics: dict, cb: callable):
        code = diagnostics['code']
        if code in self._diagnostics_cache:
            return self._diagnostics_cache[code]

        if code not in self._resolve_queue:
            self._resolve_queue[code] = [cb]
        else:
            self._resolve_queue[code].append(cb)
            return

        self._language_server.send_request("textDocument/codeAction", {
            'textDocument': {
                "uri": file
            },
            "range": diagnostics['range'],
            "context": {
                "diagnostics": diagnostics
            }
        }, self._on_rule_desc)

    def _on_rule_desc(self, responses):
        for response in responses:
            code, description, html, type, severity = response["arguments"]
            if code in self._resolve_queue:
                for cb in self._resolve_queue[code]:
                    cb(code, description, html, type, severity)
                del self._resolve_queue[code]


class SonarLintProcess(threading.Thread):
    def __init__(self, port, ls_jar, analyzers, java_bin):
        super().__init__(target=self._run_sonarlint_ls)
        self.analyzers = analyzers
        self.java_bin = java_bin
        self.ls_jar = ls_jar
        self.port = port
        self._stop_e = threading.Event()
        self._is_stop = threading.Event()

    def get_sonar_analyzers(self):
        return ["file://" + analyzer for analyzer in self.analyzers]

    def _run_sonarlint_ls(self):
        cmd = [self.java_bin, "-jar", self.ls_jar, str(self.port)]
        cmd.extend(self.get_sonar_analyzers())
        with open(os.devnull, 'w') as devnull:
            with subprocess.Popen(cmd, stdout=devnull, stderr=devnull) as proc:
                self._stop_e.wait()
                proc.terminate()
                proc.wait()
            self._is_stop.set()

    def stop(self):
        self._stop_e.set()
        self._is_stop.wait()


class Analysis:
    def __init__(self, ls_client, rule_resolver: SonarLintRuleResolver, files: list, cb: callable, done: callable):
        self._files = files
        self._pending_files = []
        self._results = []
        self._ls_client = ls_client
        self._ls_client.on('textDocument/publishDiagnostics', self._on_diagnostics)
        self._rule_resolver = rule_resolver
        self._callback = ensure_callable(cb)
        self._done_callback = ensure_callable(done)

    def run(self):
        self._ls_client.send_request("initialize", {
            "processId": os.getpid(),
            "rootUri": os.path.commonpath(self._files),
            "capabilities": {},
            "initializationOptions": {
                "disableTelemetry": True,
                "includeRuleDetailsInCodeAction": True,
                "typeScriptLocation": "/usr/lib/node_modules/typescript/lib"
            }
        }, self._send_files)

    def _send_files(self, _init_result):
        for file in self._files:
            with open(str(file), "r") as fd:
                uri = urify(file)
                self._ls_client.send_notification("textDocument/didOpen", {
                    "textDocument": {
                        "uri": uri,
                        "languageId": get_language_id(file),
                        "version": 1,
                        "text": fd.read()
                    }
                })
                self._pending_files.append(uri)

    def _on_diagnostics(self, params: dict):
        file = params['uri']
        diagnostics = params['diagnostics']
        if file not in self._pending_files:
            return

        resolved = 0
        rules = {}

        # scoping function that will resolve all callbacks
        def resolve_callbacks():
            if len(diagnostics) == resolved:
                combined = {"uri": file, "diagnostics": diagnostics, "rules": rules}
                self._results.append(combined)
                self._callback(file, combined)
                self._pending_files.remove(file)
                if len(self._pending_files) == 0:
                    # resolve completely if all files have been analyzed
                    self._done_callback(self._results)

        # scoping function that calls the callback with the file, diagnostics and rule details
        def on_rule(code, description, html, type, severity):
            nonlocal resolved
            resolved += 1
            rules[code] = {code: code, description: description, html: html, type: type, severity: severity}
            resolve_callbacks()

        # in case there is no diagnostics for this file we can already resolve
        if len(diagnostics) == 0:
            resolve_callbacks()

        for diagnostic in diagnostics:
            self._rule_resolver.get_by_diagnostics(file, diagnostic, on_rule)


def analyze(ls_client, rule_resolver, files, done_callback = None, each_callback = None) -> Analysis:
    analysis = Analysis(ls_client, rule_resolver, files, each_callback, done_callback)
    analysis.run()
    return analysis
