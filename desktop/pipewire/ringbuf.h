/* ringbuf.h - lock-free SPSC byte ring buffer.
 *
 * Exactly one producer thread and one consumer thread. The producer is the
 * socket reader (PCM arriving over ADB/USB), the consumer is the PipeWire
 * realtime process callback. 64-bit head/tail counters with acquire/release
 * ordering; buffer length must be a power of two.
 */
#ifndef AUM_RINGBUF_H
#define AUM_RINGBUF_H

#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include <stdatomic.h>

typedef struct {
    uint8_t *data;
    size_t   mask;          /* capacity - 1, capacity is a power of two */
    _Atomic uint64_t head;  /* written by producer */
    _Atomic uint64_t tail;  /* written by consumer */
    _Atomic uint64_t overruns;
} aum_ringbuf;

static inline int aum_ringbuf_init(aum_ringbuf *rb, size_t capacity_pow2)
{
    if (capacity_pow2 == 0 || (capacity_pow2 & (capacity_pow2 - 1)) != 0)
        return -1;
    rb->data = (uint8_t *)malloc(capacity_pow2);
    if (!rb->data)
        return -1;
    rb->mask = capacity_pow2 - 1;
    atomic_store_explicit(&rb->head, 0, memory_order_relaxed);
    atomic_store_explicit(&rb->tail, 0, memory_order_relaxed);
    atomic_store_explicit(&rb->overruns, 0, memory_order_relaxed);
    return 0;
}

static inline void aum_ringbuf_free(aum_ringbuf *rb)
{
    free(rb->data);
    rb->data = NULL;
}

static inline size_t aum_ringbuf_used(const aum_ringbuf *rb)
{
    uint64_t h = atomic_load_explicit(&rb->head, memory_order_acquire);
    uint64_t t = atomic_load_explicit(&rb->tail, memory_order_acquire);
    return (size_t)(h - t);
}

static inline size_t aum_ringbuf_space(const aum_ringbuf *rb)
{
    return rb->mask + 1 - aum_ringbuf_used(rb);
}

/* Producer side. Returns number of bytes actually written (drops the rest and
 * counts an overrun if the buffer is full - we prefer dropping oldest-free
 * newest-kept? No: simplest correct policy is drop-new, producer must drain
 * less; the transport layer reacts to overruns by logging them). */
static inline size_t aum_ringbuf_write(aum_ringbuf *rb, const uint8_t *src, size_t n)
{
    size_t space = aum_ringbuf_space(rb);
    if (n > space) {
        atomic_fetch_add_explicit(&rb->overruns, 1, memory_order_relaxed);
        n = space;
        if (n == 0)
            return 0;
    }
    uint64_t h = atomic_load_explicit(&rb->head, memory_order_relaxed);
    size_t pos = (size_t)(h & rb->mask);
    size_t first = rb->mask + 1 - pos;
    if (first > n)
        first = n;
    memcpy(rb->data + pos, src, first);
    if (n > first)
        memcpy(rb->data, src + first, n - first);
    atomic_store_explicit(&rb->head, h + n, memory_order_release);
    return n;
}

/* Consumer side (realtime thread): never blocks. */
static inline size_t aum_ringbuf_read(aum_ringbuf *rb, uint8_t *dst, size_t n)
{
    size_t used = aum_ringbuf_used(rb);
    if (n > used)
        n = used;
    if (n == 0)
        return 0;
    uint64_t t = atomic_load_explicit(&rb->tail, memory_order_relaxed);
    size_t pos = (size_t)(t & rb->mask);
    size_t first = rb->mask + 1 - pos;
    if (first > n)
        first = n;
    memcpy(dst, rb->data + pos, first);
    if (n > first)
        memcpy(dst + first, rb->data, n - first);
    atomic_store_explicit(&rb->tail, t + n, memory_order_release);
    return n;
}

/* Consumer side: drop everything currently buffered (used on reconnect). */
static inline void aum_ringbuf_reset(aum_ringbuf *rb)
{
    uint64_t h = atomic_load_explicit(&rb->head, memory_order_acquire);
    atomic_store_explicit(&rb->tail, h, memory_order_release);
}

#endif /* AUM_RINGBUF_H */
