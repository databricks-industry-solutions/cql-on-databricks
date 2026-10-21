# Getting started

Build and test the compiler locally.

## Prerequisites

- JDK 17, with `JAVA_HOME` set. The build targets Java 17 bytecode.
- The Gradle wrapper, `./gradlew`. It fetches Scala 2.13, Spark 4.1.2 (`compileOnly`) and cqframework
  4.8.0 (`gradle.properties`).

## Build and test

```bash
./gradlew :compiler:core:test           # compiler unit tests, no Spark server
./gradlew :compiler:conformance:test    # conformance suite
./gradlew checkAll                      # everything, including the client tests
```

`:compiler:conformance:test` translates the CQL corpus to ELM first (`translateCorpus`).

Gradle's continuous mode re-runs tests as you edit:

```bash
./gradlew -t :compiler:core:test
./gradlew -t :compiler:core:test --tests "*Bool3Spec*"
```

Reports are at `<project>/build/reports/tests/test/index.html`.

## Spark Connect server

Tests that run SQL need a Spark Connect server at `sc://localhost:15002`. Start one in another shell:

```bash
./gradlew :compiler:conformance:connectServer
```

Without a server those tests skip, so the rest of the suite runs offline. To fail instead of skip:

```bash
./gradlew :compiler:conformance:test -Dcqldatabricks.requireConnect=true
```

CI runs this way. `-Dcqldatabricks.sparkRemote=<url>` points the suite at another Spark Connect endpoint.

## API docs

```bash
./gradlew aggregateScaladoc              # every Scala project
./gradlew :compiler:core:scaladoc        # one project
```

Output is at `<project>/build/docs/scaladoc/index.html`.

## Behind a Maven mirror

If Maven Central is blocked, pass `-PmavenRepoUrl=<mirror-url>` to every Gradle command, or set
`mavenRepoUrl` in `~/.gradle/gradle.properties`.
