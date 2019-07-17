#! /usr/bin/env python3
import json
import sys
import urllib.request
from pathlib import Path

import click
import os
import threading

from sonarlintcli import languageserver, sonarlint

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
SONARLINT_CLI_HOME = str(Path.home()) + "/.sonarlint-cli"
SONARLINT_DIR = SONARLINT_CLI_HOME + "/sonarlint"
SONARLINT_LS_DIR = SONARLINT_DIR + "/server"
DEFAULT_LS_JAR = SONARLINT_LS_DIR + "/sonarlint-ls.jar"
DEFAULT_ANALYZERS_DIR = SONARLINT_DIR + "/analyzers"


def download_if_needed(url, destination):
    if os.path.isfile(destination):
        return True
    print("Downloading %s..." % (os.path.basename(destination)))
    urllib.request.urlretrieve(url, destination)


def mkdir_required():
    os.makedirs(SONARLINT_CLI_HOME, exist_ok=True)
    os.makedirs(SONARLINT_DIR, exist_ok=True)
    os.makedirs(SONARLINT_LS_DIR, exist_ok=True)
    os.makedirs(DEFAULT_ANALYZERS_DIR, exist_ok=True)


def get_files_by_ext(path: str, extensions: list) -> list:
    files = []
    for extension in extensions:
        files.extend([str(path) for path in Path(path).glob("./**/*.%s" % extension)])
    return files


def get_files_by_glob(pattern) -> list:
    if type(pattern) is list:
        files = []
        for g in pattern:
            files.extend(get_files_by_glob(g))
        return files

    if pattern == '':
        return []

    if '*' not in pattern:
        return [pattern]
    if pattern.startswith('/'):
        files = Path('/').glob(pattern[1:])
    else:
        files = Path('.').glob(pattern)
    return [str(file.absolute()) for file in files]


def download_analyzers():
    # Download all jars
    mkdir_required()
    download_if_needed(sonarlint.JAR_DOWNLOAD_LANGUAGE_SERVER, DEFAULT_LS_JAR)
    for language, jar in sonarlint.JAR_DOWNLOAD_LANGUAGES.items():
        plugin_jar_path = DEFAULT_ANALYZERS_DIR + "/" + os.path.basename(sonarlint.JAR_DOWNLOAD_LANGUAGES[language])
        download_if_needed(sonarlint.JAR_DOWNLOAD_LANGUAGES[language], plugin_jar_path)

@click.group()
def main():
    pass


@main.command()
def prefetch():
    download_analyzers()


@main.command()
@click.argument("files", nargs=-1)
@click.option("--java-bin", default='/usr/bin/java')
@click.option("--output")
def analyse(files, java_bin, output):
    files = get_files_by_glob(list(files))
    if len(files) == 0:
        click.echo("[]")
        return True

    download_analyzers()

    done = threading.Event()

    def save_lint_result(results):
        json_result = json.dumps(results, indent=4)
        if output is None:
            sys.stdout.write(json_result)
        else:
            with open(output, "w") as handle:
                handle.write(json_result)

    def finish(lint_results):
        save_lint_result(lint_results)
        done.set()

    def on_connection(server: languageserver.ReverseServer, socket):
        rule_resolver = sonarlint.SonarLintRuleResolver(server)
        sonarlint.analyze(
            server,
            rule_resolver,
            files,
            done_callback=finish
        )

    with languageserver.ReverseServer(on_connection=on_connection) as server:
        sonar_process = sonarlint.SonarLintProcess(
            port=server.addr[1],
            ls_jar=DEFAULT_LS_JAR,
            analyzers=get_files_by_ext(DEFAULT_ANALYZERS_DIR, ['jar']),
            java_bin=java_bin
        )
        sonar_process.start()
        bg_server = threading.Thread(target=server.start)
        bg_server.start()
        # Wait until done flag has been set after one analysis and stop server and SonarLint LS process
        done.wait()
        server.stop()
        sonar_process.stop()
