package com.aum.mic

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder.AudioSource

/**
 * Captures PCM 16-bit mono from the phone microphone with AudioRecord and
 * pushes framed chunks to the transport sink. Runs entirely on the capture
 * thread owned by the service; no UI involvement.
 */
class AudioStreamer(
    private val state: StreamerState,
    private val onLevel: (rms: Float) -> Unit,
    private val onMuted: (() -> Unit)? = null,
) {
    companion object {
        private const val RATE = StreamerState.SAMPLE_RATE
        private const val CHANNELS = 1
        // ~21.3 ms per chunk: 1024 frames
        private const val CHUNK_FRAMES = 1024
        private const val LEVEL_EVERY = 5 // chunks between level events (~107 ms)
    }

    @SuppressLint("MissingPermission") // service verifies RECORD_AUDIO before start
    fun stream(sendChunk: (ByteArray) -> Unit) {
        val minBuf = AudioRecord.getMinBufferSize(
            RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        if (minBuf <= 0) {
            throw IllegalStateException("AudioRecord.getMinBufferSize failed: $minBuf")
        }
        val bufSize = maxOf(minBuf * 2, CHUNK_FRAMES * 8)
        val sources = intArrayOf(
            AudioSource.VOICE_RECOGNITION,   // flat response, no AGC surprises
            AudioSource.MIC,
            AudioSource.DEFAULT
        )
        var record: AudioRecord? = null
        for (src in sources) {
            try {
                val r = AudioRecord(src, RATE, AudioFormat.CHANNEL_IN_MONO,
                    AudioFormat.ENCODING_PCM_16BIT, bufSize)
                if (r.state == AudioRecord.STATE_INITIALIZED) { record = r; break }
                r.release()
            } catch (e: Exception) {
                // try next source
            }
        }
        record ?: throw IllegalStateException(
            "Unable to initialize AudioRecord (microphone permission denied or busy)")

        val chunkBytes = CHUNK_FRAMES * 2
        val buf = ByteArray(chunkBytes)
        var sent = 0L
        var levelAcc = 0.0
        var levelN = 0
        var mutedChunks = 0   // consecutive all-zero chunks while a desktop is linked
        try {
            record.startRecording()
            while (!Thread.currentThread().isInterrupted) {
                val n = record.read(buf, 0, chunkBytes)
                if (n < 0) throw IllegalStateException("AudioRecord.read failed: $n")
                if (n == 0) continue

                // Android mutes us with exact zeros when the screen goes off;
                // a live-but-quiet room still has a few LSBs of noise.
                var allZero = true
                for (i in 0 until n step 2) {
                    if (buf[i].toInt() != 0 || buf[i + 1].toInt() != 0) { allZero = false; break }
                }
                mutedChunks = if (allZero && StreamerState.linkUp) mutedChunks + 1 else 0
                if (mutedChunks == 125) {          // ~2.7 s of exact zeros
                    onMuted?.invoke()
                }

                // RMS over samples for the level meter
                var acc = 0.0
                var i = 0
                while (i + 1 < n) {
                    val v = ((buf[i].toInt() and 0xff) or (buf[i + 1].toInt() shl 8)).toShort().toInt()
                    acc += v.toDouble() * v
                    i += 2
                }
                val frames = n / 2
                levelAcc += acc
                levelN += frames

                sendChunk(buf.copyOf(n))
                sent += frames
                StreamerState.framesSent = sent

                if (sent % LEVEL_EVERY == 0L) {
                    val rms = kotlin.math.sqrt((levelAcc / levelN.coerceAtLeast(1))).toFloat()
                    val norm = (rms / 3000f).coerceIn(0f, 1f) // ~-10 dBFS -> full scale
                    StreamerState.level = norm
                    onLevel(norm)
                    levelAcc = 0.0
                    levelN = 0
                    StreamerState.notifyUi()
                }
            }
        } finally {
            try { record.stop() } catch (ignored: Exception) {}
            record.release()
        }
    }
}
