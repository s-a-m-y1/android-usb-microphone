package com.aum.poc;

import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder.AudioSource;

import java.io.BufferedOutputStream;
import java.io.IOException;
import java.io.OutputStream;

/**
 * Shell-uid microphone streaming over `adb exec-out` (no APK install needed).
 *
 * Runs on the phone as: app_process -Djava.class.path=/data/local/tmp/aum-stream.jar
 *                      /system/bin com.aum.poc.MicStream [rate]
 *
 * com.android.shell holds RECORD_AUDIO (granted at system level), so this
 * captures the real microphone without root and without installing anything.
 *
 * Output protocol (identical framing to the desktop daemon):
 *   [u8 type][u8 flags][u16 len-le][payload]
 *   0x02 JSON handshake, then continuous 0x01 PCM16LE frames.
 * The host ends the stream by closing adb exec-out; the write then fails and
 * we exit.
 */
public final class MicStream {

    private static final byte TYPE_PCM = 0x01;
    private static final byte TYPE_JSON = 0x02;
    private static final int CHUNK_FRAMES = 1024;

    public static void main(String[] args) throws Exception {
        int rate = args.length > 0 ? Integer.parseInt(args[0]) : 48000;

        int minBuf = AudioRecord.getMinBufferSize(rate,
                AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
        if (minBuf <= 0) throw new IOException("getMinBufferSize failed: " + minBuf);
        int bufSize = Math.max(minBuf * 2, CHUNK_FRAMES * 8);

        int[] sources = { AudioSource.VOICE_RECOGNITION, AudioSource.MIC,
                          AudioSource.DEFAULT };
        AudioRecord record = null;
        for (int src : sources) {
            try {
                AudioRecord r = new AudioRecord(src, rate,
                        AudioFormat.CHANNEL_IN_MONO,
                        AudioFormat.ENCODING_PCM_16BIT, bufSize);
                if (r.getState() == AudioRecord.STATE_INITIALIZED) { record = r; break; }
                r.release();
            } catch (Exception e) { /* try next */ }
        }
        if (record == null) {
            System.err.println("[aum] ERROR: AudioRecord unavailable");
            System.exit(3);
        }

        OutputStream out = new BufferedOutputStream(System.out, 1 << 16);
        final AudioRecord rec = record;
        Runtime.getRuntime().addShutdownHook(new Thread(() -> {
            try { rec.stop(); } catch (Exception ignored) {}
            rec.release();
        }));

        byte[] hello = ("{\"event\":\"hello\",\"rate\":" + rate +
                ",\"channels\":1,\"format\":\"s16le\",\"app\":\"exec-out\"}")
                .getBytes("UTF-8");
        out.write(new byte[]{ TYPE_JSON, 0,
                (byte)(hello.length & 0xff), (byte)((hello.length >> 8) & 0xff) });
        out.write(hello);

        record.startRecording();
        byte[] chunk = new byte[CHUNK_FRAMES * 2];
        byte[] hdr = new byte[4];
        while (true) {
            int n = record.read(chunk, 0, chunk.length);
            if (n <= 0) break;
            hdr[0] = TYPE_PCM; hdr[1] = 0;
            hdr[2] = (byte)(n & 0xff); hdr[3] = (byte)((n >> 8) & 0xff);
            out.write(hdr);
            out.write(chunk, 0, n);
            out.flush();
        }
    }
}
