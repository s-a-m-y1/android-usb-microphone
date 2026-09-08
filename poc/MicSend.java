package com.aum.poc;

import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;

import java.io.BufferedOutputStream;
import java.io.FileOutputStream;
import java.io.OutputStream;

/**
 * Phase-1 proof of concept: capture microphone PCM on the device and write it
 * to stdout (raw PCM16LE), so the host can pull it over USB via `adb exec-out`.
 *
 * Runs under shell uid via app_process (com.android.shell holds RECORD_AUDIO),
 * so no APK installation is required for this test.
 *
 * Usage: app_process -Djava.class.path=/data/local/tmp/aum-poc.jar /system/bin \
 *            com.aum.poc.MicSend [rate] [seconds] [outfile]
 * If outfile is "-", PCM goes to stdout.
 */
public final class MicSend {

    public static void main(String[] args) throws Exception {
        int rate = args.length > 0 ? Integer.parseInt(args[0]) : 48000;
        int seconds = args.length > 1 ? Integer.parseInt(args[1]) : 5;
        String out = args.length > 2 ? args[2] : "-";

        int channel = AudioFormat.CHANNEL_IN_MONO;
        int encoding = AudioFormat.ENCODING_PCM_16BIT;
        int minBuf = AudioRecord.getMinBufferSize(rate, channel, encoding);
        if (minBuf <= 0) {
            System.err.println("[aum-poc] getMinBufferSize failed: " + minBuf);
            System.exit(2);
        }
        int bufSize = Math.max(minBuf * 2, rate); // >= ~21ms... actually rate bytes = 0.5s of mono16? rate bytes = rate samples * 2B... keep simple

        int[] sources = {
                MediaRecorder.AudioSource.VOICE_RECOGNITION,
                MediaRecorder.AudioSource.MIC,
                MediaRecorder.AudioSource.DEFAULT
        };

        AudioRecord record = null;
        String usedSource = null;
        for (int src : sources) {
            try {
                record = new AudioRecord(src, rate, channel, encoding, bufSize);
                if (record.getState() == AudioRecord.STATE_INITIALIZED) {
                    usedSource = src == MediaRecorder.AudioSource.VOICE_RECOGNITION ? "VOICE_RECOGNITION"
                            : src == MediaRecorder.AudioSource.MIC ? "MIC" : "DEFAULT";
                    break;
                }
                record.release();
                record = null;
            } catch (Exception e) {
                System.err.println("[aum-poc] source " + src + " failed: " + e);
            }
        }
        if (record == null) {
            System.err.println("[aum-poc] ERROR: no AudioRecord source could be initialized");
            System.exit(3);
        }

        OutputStream os = "-".equals(out)
                ? new BufferedOutputStream(System.out, 1 << 16)
                : new BufferedOutputStream(new FileOutputStream(out), 1 << 16);

        System.err.println("[aum-poc] recording rate=" + rate + " source=" + usedSource
                + " seconds=" + seconds + " buf=" + bufSize);

        try {
            record.startRecording();
            long framesTarget = (long) rate * seconds;
            long framesDone = 0;
            byte[] chunk = new byte[4096];
            while (framesDone < framesTarget) {
                int n = record.read(chunk, 0, chunk.length);
                if (n < 0) {
                    System.err.println("[aum-poc] read error: " + n);
                    break;
                }
                os.write(chunk, 0, n);
                framesDone += n / 2;
            }
        } catch (Exception e) {
            System.err.println("[aum-poc] stream aborted: " + e);
        } finally {
            try { os.flush(); } catch (Exception ignored) { }
            try { record.stop(); } catch (Exception ignored) { }
            record.release();
        }
        System.err.println("[aum-poc] done");
    }
}
