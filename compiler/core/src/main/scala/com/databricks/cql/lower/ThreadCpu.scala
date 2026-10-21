package com.databricks.cql.lower

import java.lang.management.ManagementFactory

/** Calling-thread CPU nanoseconds, for the CPU-vs-wait timing split (`it.Timing`). While a thread is
  * blocked on a Connect socket read (a `.schema` analyze, a `localCheckpoint`, a `collect`) it burns no
  * CPU, so a CPU delta naturally excludes those waits — the property the timing model relies on to
  * separate "our compile CPU" from "blocked on the server". Lowering is single-threaded, so a per-thread
  * read is exact; `nanos()` returns -1 when the JVM cannot report thread CPU time (callers fall back to
  * wall). */
object ThreadCpu {
  private val bean = ManagementFactory.getThreadMXBean
  if (bean.isThreadCpuTimeSupported && !bean.isThreadCpuTimeEnabled) bean.setThreadCpuTimeEnabled(true)

  /** Current calling-thread CPU time in nanos, or -1 if unsupported. */
  def nanos(): Long = if (bean.isThreadCpuTimeSupported) bean.getCurrentThreadCpuTime else -1L
}
