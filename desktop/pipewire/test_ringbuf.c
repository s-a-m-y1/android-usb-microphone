/* Unit tests for the lock-free SPSC ring buffer (ringbuf.h). */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <pthread.h>
#include <sched.h>
#include <stdatomic.h>
#include "ringbuf.h"

static aum_ringbuf rb;

static void test_init_and_bounds(void)
{
    assert(aum_ringbuf_init(&rb, 100) == -1);   /* not a power of two */
    assert(aum_ringbuf_init(&rb, 0) == -1);
    assert(aum_ringbuf_init(&rb, 1024) == 0);
    assert(aum_ringbuf_used(&rb) == 0);
    assert(aum_ringbuf_space(&rb) == 1024);
    printf("ok: init and bounds\n");
}

static void test_wraparound(void)
{
    uint8_t out[8];
    assert(aum_ringbuf_write(&rb, (const uint8_t *)"abcdef", 6) == 6);
    assert(aum_ringbuf_read(&rb, out, 6) == 6);
    assert(memcmp(out, "abcdef", 6) == 0);
    assert(aum_ringbuf_used(&rb) == 0);

    /* push head near the end, then wrap */
    for (int i = 0; i < 340; i++) {
        uint8_t b = (uint8_t)i;
        assert(aum_ringbuf_write(&rb, &b, 1) == 1);
        assert(aum_ringbuf_read(&rb, out, 1) == 1);
        assert(out[0] == b);
    }
    printf("ok: wraparound\n");
}

static void test_overrun_drops(void)
{
    uint8_t big[2048];
    memset(big, 0xAB, sizeof(big));
    size_t w = aum_ringbuf_write(&rb, big, sizeof(big)); /* > space (1024) */
    assert(w == 1024);
    assert(aum_ringbuf_used(&rb) == 1024);
    assert(aum_ringbuf_space(&rb) == 0);
    aum_ringbuf_reset(&rb);
    assert(aum_ringbuf_used(&rb) == 0);
    printf("ok: overrun drop policy\n");
}

static void test_reset_after_disconnect(void)
{
    uint8_t buf[512];
    memset(buf, 0x5A, sizeof(buf));
    /* emulate stale audio left in the ring from a previous client */
    assert(aum_ringbuf_write(&rb, buf, sizeof(buf)) == 512);
    assert(aum_ringbuf_used(&rb) == 512);
    aum_ringbuf_reset(&rb);
    assert(aum_ringbuf_read(&rb, buf, 8) == 0);
    printf("ok: reset drops stale audio\n");
}

/* concurrent producer/consumer: 4 MB must survive the round trip */
static _Atomic long produced, consumed;
#define BLK 256
static void *producer(void *arg)
{
    (void)arg;
    uint8_t buf[BLK];
    long sent = 0;
    while (sent < 4L * 1024 * 1024) {
        for (int i = 0; i < BLK; i++)
            buf[i] = (uint8_t)((sent + i) & 0xff);
        size_t w = aum_ringbuf_write(&rb, buf, BLK);
        if (w == 0) {
            sched_yield();   /* full: consumer will drain */
        } else {
            atomic_fetch_add(&produced, (long)w);
            sent += (long)w;
        }
    }
    return NULL;
}
static void *consumer(void *arg)
{
    (void)arg;
    uint8_t got[BLK];
    uint8_t expect = 0;
    long received = 0;
    while (atomic_load(&consumed) < 4L * 1024 * 1024) {
        size_t n = aum_ringbuf_read(&rb, got, BLK);
        if (n == 0) {
            sched_yield();
            continue;
        }
        for (size_t i = 0; i < n; i++) {
            assert(got[i] == expect);
            expect = (uint8_t)(expect + 1);
        }
        received += (long)n;
        atomic_store(&consumed, received);
    }
    return NULL;
}

static void test_concurrent(void)
{
    aum_ringbuf_reset(&rb);
    pthread_t tp, tc;
    pthread_create(&tp, NULL, producer, NULL);
    pthread_create(&tc, NULL, consumer, NULL);
    pthread_join(tp, NULL);
    pthread_join(tc, NULL);
    long p = atomic_load(&produced), c = atomic_load(&consumed);
    assert(p == 4L * 1024 * 1024);
    assert(c == 4L * 1024 * 1024);
    printf("ok: concurrent SPSC 4MB round trip (p=%ld c=%ld)\n", p, c);
}

int main(void)
{
    test_init_and_bounds();
    test_wraparound();
    test_overrun_drops();
    test_reset_after_disconnect();
    test_concurrent();
    printf("ALL TESTS PASSED\n");
    return 0;
}
