package com.aum.mic

import android.content.Context
import android.os.Handler
import android.os.Looper

/**
 * Shared observable state between the service and the activity UI.
 * All values are also mirrored to static fields so a recreated Activity can
 * restore the current view immediately.
 */
class StreamerState(private val context: Context) {

    companion object {
        const val SAMPLE_RATE = 48000

        @Volatile var serviceRunning: Boolean = false
        @Volatile var linkUp: Boolean = false     // desktop client connected
        @Volatile var level: Float = 0f           // 0..1
        @Volatile var lastError: String? = null
        @Volatile var framesSent: Long = 0L

        val listeners = mutableListOf<Runnable>()
        private val main = Handler(Looper.getMainLooper())

        fun notifyUi() {
            main.post {
                val snapshot: List<Runnable> = synchronized(listeners) { listeners.toList() }
                snapshot.forEach { it.run() }
            }
        }
    }

    val rate: Int get() = SAMPLE_RATE
    var running: Boolean
        get() = serviceRunning
        set(v) { serviceRunning = v; notifyUi() }
    var connected: Boolean
        get() = linkUp
        set(v) { linkUp = v; if (!v) level = 0f; notifyUi() }

    fun update() = notifyUi()
    fun postLink(up: Boolean) { connected = up }
    fun postError(message: String?) { lastError = message; notifyUi() }
    fun bumpLevel(rms: Float) { level = rms; framesSent++; notifyUi() }
}
