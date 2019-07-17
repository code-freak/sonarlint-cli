# SonarLint for CLI

**This is only a prototype**

Run SonarLint analyzers on CLI. Currently this simply spins up the SonarLint language server and send all files to the
server as if they were opened in an editor. The language server than publishes diagnostics for each file which are
saved/printed as JSON.

## Install
**You can also use the Docker image instead of downloading this project! See "Usage" below**

1. Download/Clone this repository
2. Run `pip install .` inside the directory

## Usage
### With Docker
This will mount `/path/to/your/code` as `/code` inside the container. The image uses the executable as entry-point.
Make sure you wrap the file-glob inside quotes so it gets evaluated in the container and not on your host.
```
$ docker run --rm -it -v /path/to/your/code:/code cfreak/sonarlint-cli "/code/your/files/**/*.[java|kt|...]"
```

### Locally after installation
```
$ sonarlint-cli analyse /path/to/your/code/**/*.[java|kt|...]
```

## Included analyzers
* HTML
* JavaScript
* PHP
* Python
* TypeScript
* Kotlin
* Java

## TODO
* Switch to Python's `asyncio`
* Support other language servers (e.g. `clangd`)
* Refactoring, Polishing, …
