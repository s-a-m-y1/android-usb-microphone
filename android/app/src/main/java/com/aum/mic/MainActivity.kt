package com.aum.mic

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.widget.Button
import android.widget.ProgressBar
import android.widget.TextView

/**
 * Minimal single-screen UI:
 *
 *   Android USB Microphone
 *   USB      ● Connected / ○ Waiting for desktop
 *   Mic      [level bar]
 *   Rate     48000 Hz
 *   [ Start ] [ Stop ]
 */
class MainActivity : Activity() {

    private lateinit var statusText: TextView
    private lateinit var levelBar: ProgressBar
    private lateinit var rateText: TextView
    private lateinit var infoText: TextView
    private lateinit var startBtn: Button
    private lateinit var stopBtn: Button

    private val uiRefresh = Runnable { refresh() }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        statusText = findViewById(R.id.statusText)
        levelBar = findViewById(R.id.levelBar)
        rateText = findViewById(R.id.rateText)
        infoText = findViewById(R.id.infoText)
        startBtn = findViewById(R.id.startButton)
        stopBtn = findViewById(R.id.stopButton)

        rateText.text = getString(R.string.rate_fmt, StreamerState.SAMPLE_RATE)

        startBtn.setOnClickListener { startStreaming() }
        stopBtn.setOnClickListener { stopStreaming() }

        val wantStart = intent.getBooleanExtra(MicService.EXTRA_AUTOSTART, false) ||
            intent.getStringExtra(MicService.EXTRA_AUTOSTART) == "true"
        if (wantStart) {
            startStreaming()
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        val wantStart = intent.getBooleanExtra(MicService.EXTRA_AUTOSTART, false) ||
            intent.getStringExtra(MicService.EXTRA_AUTOSTART) == "true"
        if (wantStart && !StreamerState.serviceRunning) {
            startStreaming()
        }
    }

    override fun onResume() {
        super.onResume()
        synchronized(StreamerState.listeners) {
            StreamerState.listeners.add(uiRefresh)
        }
        refresh()
    }

    override fun onPause() {
        synchronized(StreamerState.listeners) {
            StreamerState.listeners.remove(uiRefresh)
        }
        super.onPause()
    }

    private fun refresh() {
        val running = StreamerState.serviceRunning
        val linked = StreamerState.linkUp
        statusText.text = when {
            running && linked -> getString(R.string.status_streaming)
            running -> getString(R.string.status_waiting)
            else -> getString(R.string.status_stopped)
        }
        levelBar.progress = (StreamerState.level * 100).toInt()
        startBtn.isEnabled = !running
        stopBtn.isEnabled = running

        val err = StreamerState.lastError
        infoText.text = when {
            err != null -> getString(R.string.err_fmt, err)
            running && linked -> getString(R.string.info_streaming)
            running -> getString(R.string.info_waiting)
            else -> getString(R.string.info_idle)
        }
    }

    private fun startStreaming() {
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), 1)
            return
        }
        ensureNotificationPermission()
        val i = Intent(this, MicService::class.java)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) startForegroundService(i)
        else startService(i)
    }

    private fun stopStreaming() {
        stopService(Intent(this, MicService::class.java))
        StreamerState.serviceRunning = false
        StreamerState.linkUp = false
        refresh()
    }

    private fun ensureNotificationPermission() {
        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(android.Manifest.permission.POST_NOTIFICATIONS), 2)
        }
    }

    override fun onRequestPermissionsResult(
        requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == 1 &&
            grantResults.isNotEmpty() &&
            grantResults[0] == PackageManager.PERMISSION_GRANTED) {
            startStreaming()
        } else if (requestCode == 1) {
            StreamerState.lastError = getString(R.string.mic_denied)
            refresh()
        }
    }
}
