package com.aum.mic

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.LocalServerSocket
import android.net.LocalSocket
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.util.Log
import java.io.IOException
import java.io.OutputStream
import java.io.PrintWriter
import java.io.StringWriter

/**
 * Foreground service that captures microphone PCM and streams it to the
 * desktop over the ADB/USB transport.
 *
 * The phone listens on a *local abstract-namespace unix socket* named
 * "aum_mic" (no INTERNET permission is even declared, so the audio physically
 * cannot leave the device over the network). The desktop reaches it with
 * `adb forward tcp:<port> localabstract:aum_mic`, so bytes travel only over
 * the USB cable - never Wi-Fi, never Bluetooth, never the internet.
 *
 * Wire protocol (framing shared with the desktop side):
 *   phone -> host: [u8 type][u8 flags][u16 len-le][payload]
 *     type 0x02 = JSON handshake      {"event":"hello","rate":...}
 *     type 0x01 = PCM16LE payload
 *     type 0x04 = JSON event          {"event":"level","rms":...}
 */
class MicService : Service() {

    companion object {
        const val TAG = "aum"
        const val SOCKET_NAME = "aum_mic"
        const val CHANNEL_ID = "aum_mic"
        const val NOTIFICATION_ID = 1
        const val ACTION_STOP = "com.aum.mic.STOP"
        const val EXTRA_AUTOSTART = "autostart"

        const val TYPE_PCM: Byte = 0x01
        const val TYPE_JSON: Byte = 0x02
        const val TYPE_EVENT: Byte = 0x04
    }

    @Volatile private var running = false
    private var serverSocket: LocalServerSocket? = null
    private var acceptThread: Thread? = null
    private var wakeLock: PowerManager.WakeLock? = null
    lateinit var state: StreamerState
        private set

    override fun onCreate() {
        super.onCreate()
        state = StreamerState(this)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopSelf()
            return START_NOT_STICKY
        }
        startAsForeground()
        keepScreenOn()
        Log.i(TAG, "onStartCommand: foreground started, running=$running")
        if (!running) {
            running = true
            state.running = true
            state.update()
            acceptThread = Thread(::acceptLoop, "aum-accept").apply { start() }
        }
        return START_STICKY
    }

    private fun startAsForeground() {
        val nm = getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            nm.createNotificationChannel(
                NotificationChannel(CHANNEL_ID, getString(R.string.notif_title),
                    NotificationManager.IMPORTANCE_LOW)
            )
        }
        val b = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O)
            Notification.Builder(this, CHANNEL_ID) else Notification.Builder(this)
        b.setContentTitle(getString(R.string.notif_title))
            .setContentText(getString(R.string.notif_text))
            .setSmallIcon(R.drawable.ic_mic)
            .setOngoing(true)
        val notification: Notification = b.build()
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                startForeground(NOTIFICATION_ID, notification,
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
            } else {
                startForeground(NOTIFICATION_ID, notification)
            }
            Log.i(TAG, "startForeground OK (mic type)")
        } catch (t: Throwable) {
            Log.e(TAG, "startForeground failed", t)
            throw t
        }
    }

    /**
     * Android silences microphone access for an app the moment the screen
     * goes off (while-in-use policy). The phone is on USB power while
     * streaming, so we hold a screen wake lock for as long as the service
     * runs - this keeps the mic hot with the phone locked or app in
     * background.
     */
    private fun keepScreenOn() {
        if (wakeLock?.isHeld == true) return
        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        wakeLock = pm.newWakeLock(PowerManager.SCREEN_BRIGHT_WAKE_LOCK,
            "aum:mic-stream").apply {
            setReferenceCounted(false)
            acquire(12 * 60 * 60 * 1000L)   // effectively until stop
        }
        Log.i(TAG, "screen wake lock held")
    }

    /**
     * Android mutes the microphone as soon as the screen goes off (privacy
     * policy - the platform reverts manual appops overrides within seconds).
     * When the desktop is actively receiving but every sample is exactly
     * zero, we know we've been muted: wake the screen back up so the stream
     * self-heals. The phone is on USB power, so this is free.
     */
    private fun wakeScreenForMic() {
        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        pm.newWakeLock(
            PowerManager.FULL_WAKE_LOCK or PowerManager.ACQUIRE_CAUSES_WAKEUP,
            "aum:mic-unmute").apply {
            setReferenceCounted(false)
            acquire(60_000L)
        }
        keepScreenOn()
        Log.i(TAG, "mic muted with screen off - woke the screen")
    }

    private fun releaseScreenOn() {
        try { wakeLock?.release() } catch (ignored: Exception) {}
        wakeLock = null
    }

    /** Accept loop: one desktop client at a time; reconnects cleanly. */
    private fun acceptLoop() {
        Log.i(TAG, "accept loop starting")
        while (running) {
            try {
                val server = serverSocket ?: LocalServerSocket(SOCKET_NAME).also {
                    serverSocket = it
                    Log.i(TAG, "listening on abstract socket $SOCKET_NAME")
                }
                val client = server.accept()   // blocks
                Log.i(TAG, "client connected")
                if (!running) { client.close(); break }
                handleClient(client)
            } catch (e: IOException) {
                if (!running) break
                Log.w(TAG, "accept failed: $e")
                closeServer()
                state.postError(null)
                sleepQuiet(500)
            } catch (t: Throwable) {
                Log.e(TAG, "accept loop error", t)
                state.postError(throwableText(t))
                sleepQuiet(1000)
            }
        }
        closeServer()
    }

    private fun handleClient(client: LocalSocket) {
        Log.i(TAG, "handleClient begin")
        state.postLink(true)
        state.postError(null)
        try {
            val out = client.getOutputStream()

            fun sendFramed(type: Byte, payload: ByteArray) {
                val hdr = byteArrayOf(type, 0,
                    (payload.size and 0xff).toByte(),
                    ((payload.size shr 8) and 0xff).toByte())
                out.write(hdr)
                out.write(payload)
                out.flush()
            }

            sendFramed(TYPE_JSON,
                ("{\"event\":\"hello\",\"rate\":${state.rate}," +
                 "\"channels\":1,\"format\":\"s16le\"}").toByteArray())

            val streamer = AudioStreamer(state,
                onLevel = { rms ->
                    sendFramed(TYPE_EVENT,
                        "{\"event\":\"level\",\"rms\":$rms}".toByteArray())
                },
                onMuted = { wakeScreenForMic() })
            streamer.stream { chunk ->
                sendFramed(TYPE_PCM, chunk)
            }
        } catch (e: IOException) {
            // desktop disconnected or socket broke; go back to accepting
        } catch (t: Throwable) {
            state.postError(throwableText(t))
        } finally {
            try { client.close() } catch (ignored: Exception) {}
            state.postLink(false)
        }
    }

    private fun closeServer() {
        try { serverSocket?.close() } catch (ignored: Exception) {}
        serverSocket = null
    }

    private fun sleepQuiet(ms: Long) = try { Thread.sleep(ms) } catch (e: InterruptedException) { Thread.currentThread().interrupt() }

    private fun throwableText(t: Throwable): String {
        val sw = StringWriter()
        t.printStackTrace(PrintWriter(sw))
        return t.message ?: t.toString()
    }

    override fun onDestroy() {
        running = false
        state.running = false
        state.postLink(false)
        closeServer()
        releaseScreenOn()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null
}
