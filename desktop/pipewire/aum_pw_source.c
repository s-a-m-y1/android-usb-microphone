/* aum-pw-source - Android USB Microphone PipeWire virtual audio source.
 *
 * Creates a real PipeWire source node ("Audio/Source") that appears in
 * `pactl list sources`, GNOME sound settings, OBS, Discord, browsers, ...
 *
 * It listens on a private per-user UNIX socket (under $XDG_RUNTIME_DIR, never
 * TCP, never a public interface) and accepts two kinds of frames:
 *
 *   client -> us:  [u8 type][u8 flags][u16 len-le][payload]
 *                  type 0x01 = PCM16LE payload -> pushed into the ring buffer
 *                  type 0x02 = JSON control line (e.g. {"cmd":"flush"})
 *   us -> client:  type 0x03 = JSON event line (source-ready / stats / ...)
 *
 * The PipeWire process callback (realtime thread) pulls PCM16 frames from the
 * lock-free SPSC ring buffer and outputs silence when the buffer underruns,
 * so normal USB jitter never produces clicks - only (rare, silent) gaps.
 *
 * Self-test mode: --sine produces a 440 Hz tone instead of socket input so
 * the source can be verified without the phone attached.
 */

#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <stdatomic.h>
#include <errno.h>
#include <math.h>
#include <signal.h>
#include <unistd.h>
#include <pthread.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <getopt.h>

#include <pipewire/pipewire.h>
#include <spa/utils/result.h>
#include <spa/param/audio/format-utils.h>

#include "ringbuf.h"

#define FRAME_HDR 4
#define TYPE_PCM    0x01
#define TYPE_JSON   0x02
#define TYPE_EVENT  0x03
#define MAX_PAYLOAD (1 << 20)

typedef struct {
    /* configuration */
    const char *socket_path;
    const char *node_name;
    const char *node_desc;
    uint32_t rate;
    uint32_t channels;
    uint32_t latency_frames;
    int ring_bytes_ms;
    int sine_test;
    double sine_freq;

    /* pipewire */
    struct pw_thread_loop *loop;
    struct pw_stream *stream;
    int stream_connected;
    atomic_int source_ready;

    /* audio */
    aum_ringbuf ring;
    uint32_t frame_bytes;         /* channels * 2 */
    double sine_phase;

    /* socket */
    int listen_fd;
    volatile int client_fd;       /* -1 when no client */
    pthread_t accept_thread;
    pthread_t client_thread;
    atomic_int running;
    atomic_long clients_served;

    /* stats */
    atomic_long underruns;
    atomic_long bytes_received;
    atomic_long pcm_overruns;
    atomic_long process_calls;
} aum_app;

static aum_app app;

/* ---------------------------------------------------------------- events */

static int write_all(int fd, const uint8_t *buf, size_t n)
{
    while (n > 0) {
        ssize_t w = write(fd, buf, n);
        if (w <= 0) {
            if (w < 0 && errno == EINTR)
                continue;
            return -1;
        }
        buf += w;
        n -= (size_t)w;
    }
    return 0;
}

static void send_event_json(const char *json)
{
    int fd = app.client_fd;
    if (fd < 0)
        return;
    size_t len = strlen(json);
    if (len > MAX_PAYLOAD)
        return;
    uint8_t hdr[FRAME_HDR] = { TYPE_EVENT, 0,
                               (uint8_t)(len & 0xff),
                               (uint8_t)((len >> 8) & 0xff) };
    if (write_all(fd, hdr, FRAME_HDR) < 0)
        return;
    write_all(fd, (const uint8_t *)json, len);
}

/* ---------------------------------------------------------- pipewire side */

static void on_process(void *userdata)
{
    long calls = atomic_fetch_add_explicit(&app.process_calls, 1, memory_order_relaxed);
    if ((calls % 100) == 0)
        fprintf(stderr, "[aum-pw] on_process call #%ld\n", calls + 1);
    struct pw_buffer *b = pw_stream_dequeue_buffer(app.stream);
    if (b == NULL) {
        pw_log_warn("out of buffers");
        return;
    }

    struct spa_buffer *buf = b->buffer;
    struct spa_data *d = &buf->datas[0];

    if (d->data == NULL) {
        pw_stream_queue_buffer(app.stream, b);
        return;
    }

    size_t want = d->maxsize;
    /* Fill what the graph asks for (pw_buffer.requested, in frames) if told. */
    if (b->requested != 0 && b->requested * app.frame_bytes <= d->maxsize)
        want = (size_t)b->requested * app.frame_bytes;

    size_t got = 0;
    if (app.sine_test) {
        size_t frames = want / app.frame_bytes;
        int16_t *out = (int16_t *)d->data;
        for (size_t i = 0; i < frames * app.channels; i++) {
            double s = sin(app.sine_phase);
            out[i] = (int16_t)(s * 8000.0);
            app.sine_phase += 2.0 * M_PI * app.sine_freq / (double)app.rate;
            if (app.sine_phase > 2.0 * M_PI)
                app.sine_phase -= 2.0 * M_PI;
        }
        got = want;
    } else {
        got = aum_ringbuf_read(&app.ring, (uint8_t *)d->data, want);
        if (got < want) {
            memset((uint8_t *)d->data + got, 0, want - got);
            atomic_fetch_add_explicit(&app.underruns, 1, memory_order_relaxed);
        }
    }

    d->chunk->offset = 0;
    d->chunk->stride = (int32_t)app.frame_bytes;
    d->chunk->size = (uint32_t)want;
    d->chunk->flags = 0;

    pw_stream_queue_buffer(app.stream, b);
}

static void on_state_changed(void *userdata, enum pw_stream_state old,
                             enum pw_stream_state state, const char *error)
{
    switch (state) {
    case PW_STREAM_STATE_PAUSED:
        fprintf(stderr, "[aum-pw] stream state PAUSED (old=%d)\n", (int)old);
        if (!app.stream_connected) {
            app.stream_connected = 1;
            atomic_store(&app.source_ready, 1);
            char msg[256];
            snprintf(msg, sizeof(msg),
                     "{\"event\":\"source-ready\",\"node_name\":\"%s\",\"description\":\"%s\",\"rate\":%u,\"channels\":%u}",
                     app.node_name, app.node_desc, app.rate, app.channels);
            send_event_json(msg);
            fprintf(stderr, "[aum-pw] source node ready (%s)\n", app.node_name);
        }
        break;
    case PW_STREAM_STATE_ERROR:
        fprintf(stderr, "[aum-pw] stream error: %s\n", error ? error : "unknown");
        {
            char msg[256];
            snprintf(msg, sizeof(msg),
                     "{\"event\":\"error\",\"message\":\"PipeWire stream error: %s\"}",
                     error ? error : "unknown");
            send_event_json(msg);
        }
        break;
    default:
        fprintf(stderr, "[aum-pw] stream state -> %d (old=%d)\n", (int)state, (int)old);
        break;
    }
}

static const struct pw_stream_events stream_events = {
    PW_VERSION_STREAM_EVENTS,
    .process = on_process,
    .state_changed = on_state_changed,
};

static int start_pipewire(void)
{
    const struct spa_pod *params[1];
    uint8_t pod_buf[1024];
    struct spa_pod_builder b = SPA_POD_BUILDER_INIT(pod_buf, sizeof(pod_buf));
    struct spa_audio_info_raw info;
    memset(&info, 0, sizeof(info));
    info.format = SPA_AUDIO_FORMAT_S16_LE;
    info.rate = app.rate;
    info.channels = app.channels;
    if (app.channels == 1)
        info.position[0] = SPA_AUDIO_CHANNEL_MONO;
    else {
        info.position[0] = SPA_AUDIO_CHANNEL_FL;
        info.position[1] = SPA_AUDIO_CHANNEL_FR;
    }
    params[0] = spa_format_audio_raw_build(&b, SPA_PARAM_EnumFormat, &info);

    struct pw_properties *props = pw_properties_new(
        PW_KEY_MEDIA_TYPE, "Audio",
        PW_KEY_MEDIA_CATEGORY, "Playback",
        PW_KEY_MEDIA_ROLE, "Music",
        PW_KEY_MEDIA_CLASS, "Audio/Source",
        PW_KEY_NODE_NAME, app.node_name,
        PW_KEY_NODE_DESCRIPTION, app.node_desc,
        PW_KEY_NODE_VIRTUAL, "true",
        NULL);

    char latency[64];
    snprintf(latency, sizeof(latency), "%u/%u", app.latency_frames, app.rate);
    pw_properties_set(props, PW_KEY_NODE_LATENCY, latency);

    app.loop = pw_thread_loop_new("aum-pw", NULL);
    if (app.loop == NULL) {
        fprintf(stderr, "[aum-pw] failed to create PipeWire thread loop\n");
        return -1;
    }

    app.stream = pw_stream_new_simple(pw_thread_loop_get_loop(app.loop),
                                      "android-usb-mic", props,
                                      &stream_events, NULL);
    if (app.stream == NULL) {
        fprintf(stderr, "[aum-pw] failed to create PipeWire stream\n");
        return -1;
    }

    if (pw_thread_loop_start(app.loop) < 0) {
        fprintf(stderr, "[aum-pw] failed to start PipeWire thread loop\n");
        return -1;
    }

    pw_thread_loop_lock(app.loop);
    int rc = pw_stream_connect(app.stream,
                               PW_DIRECTION_OUTPUT,
                               PW_ID_ANY,
                               PW_STREAM_FLAG_MAP_BUFFERS |
                               PW_STREAM_FLAG_RT_PROCESS,
                               params, 1);
    pw_thread_loop_unlock(app.loop);
    if (rc < 0) {
        fprintf(stderr, "[aum-pw] pw_stream_connect failed: %s\n", spa_strerror(rc));
        return rc;
    }
    return 0;
}

/* ------------------------------------------------------------ socket side */

/* Client thread: read framed data from the GUI and push PCM into the ring. */
static void *client_thread_fn(void *arg)
{
    int fd = (int)(intptr_t)arg;
    uint8_t hdr[FRAME_HDR];
    uint8_t *payload = malloc(MAX_PAYLOAD + 1);

    /* fresh client -> drop any stale audio so playback starts at "now" */
    aum_ringbuf_reset(&app.ring);
    send_event_json("{\"event\":\"client-connected\"}");

    for (;;) {
        ssize_t r = read(fd, hdr, FRAME_HDR);
        if (r <= 0) {
            if (r < 0 && errno == EINTR)
                continue;
            break;
        }
        if (r < FRAME_HDR)
            break;
        uint8_t type = hdr[0];
        uint16_t len = (uint16_t)(hdr[2] | (hdr[3] << 8));
        size_t off = 0;
        while (off < len) {
            ssize_t rr = read(fd, payload + off, len - off);
            if (rr <= 0) {
                if (rr < 0 && errno == EINTR)
                    continue;
                goto done;
            }
            off += (size_t)rr;
        }
        if (type == TYPE_PCM) {
            size_t w = aum_ringbuf_write(&app.ring, payload, len);
            atomic_fetch_add_explicit(&app.bytes_received, w, memory_order_relaxed);
            if (w < len)
                atomic_fetch_add_explicit(&app.pcm_overruns, 1, memory_order_relaxed);
        } else if (type == TYPE_JSON) {
            payload[len] = 0;
            if (memcmp(payload, "{\"cmd\":\"flush\"}", 16) == 0) {
                aum_ringbuf_reset(&app.ring);
                send_event_json("{\"event\":\"flushed\"}");
            }
        }
    }
done:
    free(payload);
    close(fd);
    app.client_fd = -1;
    fprintf(stderr, "[aum-pw] client disconnected\n");
    send_event_json("{\"event\":\"client-disconnected\"}");
    return NULL;
}

/* Accept thread: one client at a time; a new client replaces the old one. */
static void *accept_thread_fn(void *arg)
{
    (void)arg;
    while (atomic_load(&app.running)) {
        int cfd = accept(app.listen_fd, NULL, NULL);
        if (cfd < 0) {
            if (errno == EINTR)
                continue;
            if (!atomic_load(&app.running))
                break;
            continue;
        }
        int old = app.client_fd;
        if (old >= 0) {
            shutdown(old, SHUT_RDWR);   /* causes old client thread to exit */
        } else {
            atomic_fetch_add(&app.clients_served, 1);
        }
        app.client_fd = cfd;
        if (pthread_create(&app.client_thread, NULL, client_thread_fn,
                           (void *)(intptr_t)cfd) != 0) {
            close(cfd);
            app.client_fd = -1;
        }
        pthread_detach(app.client_thread);
    }
    return NULL;
}

static int start_socket(void)
{
    unlink(app.socket_path);   /* stale socket from a crashed run */
    app.listen_fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (app.listen_fd < 0)
        return -1;
    struct sockaddr_un addr;
    memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    if (strlen(app.socket_path) >= sizeof(addr.sun_path)) {
        fprintf(stderr, "[aum-pw] socket path too long\n");
        return -1;
    }
    strncpy(addr.sun_path, app.socket_path, sizeof(addr.sun_path) - 1);
    if (bind(app.listen_fd, (struct sockaddr *)&addr, sizeof(addr)) < 0)
        return -1;
    if (chmod(app.socket_path, 0600) < 0) { /* user-only access */
        /* not fatal on all filesystems */
    }
    if (listen(app.listen_fd, 2) < 0)
        return -1;
    app.client_fd = -1;
    atomic_store(&app.running, 1);
    if (pthread_create(&app.accept_thread, NULL, accept_thread_fn, NULL) != 0)
        return -1;
    return 0;
}

/* ----------------------------------------------------------------- stats */

static void *stats_thread_fn(void *arg)
{
    (void)arg;
    long last_underruns = 0;
    while (atomic_load(&app.running)) {
        struct timespec ts = { 2, 0 };
        nanosleep(&ts, NULL);
        long u = atomic_load(&app.underruns);
        long pc = atomic_load(&app.process_calls);
        long fill = (long)aum_ringbuf_used(&app.ring);
        char msg[256];
        snprintf(msg, sizeof(msg),
                 "{\"event\":\"stats\",\"ring_fill\":%ld,\"underruns\":%ld,\"new_underruns\":%ld,\"bytes_received\":%ld,\"overruns\":%ld,\"process_calls\":%ld}",
                 fill, u, u - last_underruns,
                 atomic_load(&app.bytes_received),
                 atomic_load(&app.pcm_overruns), pc);
        last_underruns = u;
        send_event_json(msg);
    }
    return NULL;
}

/* ------------------------------------------------------------------ main */

static void on_signal(int sig)
{
    (void)sig;
    atomic_store(&app.running, 0);
    /* closing the socket would block in accept(); shutdown wakes it up */
    shutdown(app.listen_fd, SHUT_RDWR);
    if (app.client_fd >= 0)
        shutdown(app.client_fd, SHUT_RDWR);
}

static void usage(const char *prog)
{
    fprintf(stderr,
        "Usage: %s [options]\n"
        "  --socket PATH      control socket path (default under $XDG_RUNTIME_DIR)\n"
        "  --name NAME        PipeWire node name (default android_usb_mic)\n"
        "  --description TXT  human-readable device name\n"
        "  --rate N           sample rate (default 48000)\n"
        "  --channels N       1 = mono, 2 = stereo (default 1)\n"
        "  --latency N        node latency in frames (default 512)\n"
        "  --ring-ms N        ring buffer duration in ms (default 120)\n"
        "  --sine             self-test: output a 440 Hz tone\n"
        "  --quiet            don't log to stderr\n",
        prog);
}

int main(int argc, char **argv)
{
    static char socket_default[256];
    const char *xrd = getenv("XDG_RUNTIME_DIR");
    snprintf(socket_default, sizeof(socket_default),
             "%s/aum-mic.sock", xrd ? xrd : "/tmp");

    app.socket_path = socket_default;
    app.node_name = "android_usb_mic";
    app.node_desc = "Android USB Microphone";
    app.rate = 48000;
    app.channels = 1;
    app.latency_frames = 512;
    app.ring_bytes_ms = 120;
    app.sine_test = 0;
    app.sine_freq = 440.0;

    static struct option longopts[] = {
        { "socket",      required_argument, NULL, 's' },
        { "name",        required_argument, NULL, 'n' },
        { "description", required_argument, NULL, 'd' },
        { "rate",        required_argument, NULL, 'r' },
        { "channels",    required_argument, NULL, 'c' },
        { "latency",     required_argument, NULL, 'l' },
        { "ring-ms",     required_argument, NULL, 'g' },
        { "sine",        no_argument,       NULL, 'S' },
        { "quiet",       no_argument,       NULL, 'q' },
        { "help",        no_argument,       NULL, 'h' },
        { NULL, 0, NULL, 0 }
    };
    int opt, quiet = 0;
    while ((opt = getopt_long(argc, argv, "s:n:d:r:c:l:g:SQh", longopts, NULL)) != -1) {
        switch (opt) {
        case 's': app.socket_path = optarg; break;
        case 'n': app.node_name = optarg; break;
        case 'd': app.node_desc = optarg; break;
        case 'r': app.rate = (uint32_t)atoi(optarg); break;
        case 'c': app.channels = (uint32_t)atoi(optarg); break;
        case 'l': app.latency_frames = (uint32_t)atoi(optarg); break;
        case 'g': app.ring_bytes_ms = atoi(optarg); break;
        case 'S': app.sine_test = 1; break;
        case 'q': quiet = 1; break;
        case 'h': usage(argv[0]); return 0;
        default:  usage(argv[0]); return 2;
        }
    }

    if (app.rate < 8000 || app.rate > 192000) {
        fprintf(stderr, "[aum-pw] invalid sample rate %u\n", app.rate);
        return 2;
    }
    if (app.channels < 1 || app.channels > 2) {
        fprintf(stderr, "[aum-pw] invalid channel count %u\n", app.channels);
        return 2;
    }

    if (quiet) {
        int fd = open("/dev/null", O_WRONLY);
        if (fd >= 0) { dup2(fd, STDERR_FILENO); close(fd); }
    }

    pw_init(&argc, &argv);

    app.frame_bytes = app.channels * 2;

    size_t ring_size = 16;
    if (app.ring_bytes_ms <= 0)
        app.ring_bytes_ms = 120;
    size_t need = (size_t)app.rate * app.frame_bytes * (size_t)app.ring_bytes_ms / 1000;
    while (ring_size < need)
        ring_size <<= 1;
    if (aum_ringbuf_init(&app.ring, ring_size) < 0) {
        fprintf(stderr, "[aum-pw] failed to allocate ring buffer\n");
        return 1;
    }

    app.listen_fd = -1;
    app.client_fd = -1;
    atomic_store(&app.running, 1);
    atomic_store(&app.source_ready, 0);

    if (start_pipewire() < 0) {
        fprintf(stderr, "Unable to create PipeWire audio source. "
                        "Is 'pipewire' and 'wireplumber' running?\n");
        return 1;
    }

    if (!app.sine_test) {
        if (start_socket() < 0) {
            fprintf(stderr, "[aum-pw] failed to create control socket %s: %s\n",
                    app.socket_path, strerror(errno));
            return 1;
        }
        pthread_t stats_thread;
        pthread_create(&stats_thread, NULL, stats_thread_fn, NULL);
        pthread_detach(stats_thread);
    }

    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);
    signal(SIGPIPE, SIG_IGN);

    size_t ring_total = aum_ringbuf_used(&app.ring) + aum_ringbuf_space(&app.ring);
    fprintf(stderr, "[aum-pw] running: rate=%u ch=%u ring=%zu bytes socket=%s\n",
            app.rate, app.channels, ring_total,
            app.sine_test ? "(none - sine test)" : app.socket_path);

    /* main supervision loop */
    while (atomic_load(&app.running))
        sleep(1);

    fprintf(stderr, "[aum-pw] shutting down\n");
    pw_thread_loop_lock(app.loop);
    pw_stream_destroy(app.stream);
    pw_thread_loop_unlock(app.loop);
    pw_thread_loop_stop(app.loop);
    pw_thread_loop_destroy(app.loop);
    if (app.listen_fd >= 0) {
        close(app.listen_fd);
        unlink(app.socket_path);
    }
    aum_ringbuf_free(&app.ring);
    pw_deinit();
    return 0;
}
