package waa

import (
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func TestShortTimersReleaseTheirRegistryEntries(t *testing.T) {
	loop := newEventLoop()
	defer loop.close()
	const producers = 8
	const perProducer = 1000
	var scheduled, completed sync.WaitGroup
	scheduled.Add(producers)
	completed.Add(producers * perProducer)
	for range producers {
		go func() {
			defer scheduled.Done()
			for range perProducer {
				loop.schedule(time.Nanosecond, completed.Done)
			}
		}()
	}
	finished := make(chan struct{})
	go func() {
		scheduled.Wait()
		completed.Wait()
		close(finished)
	}()
	select {
	case <-finished:
	case <-time.After(15 * time.Second):
		t.Fatal("short timers did not all run")
	}
	loop.mu.Lock()
	remaining := len(loop.timers)
	loop.mu.Unlock()
	if remaining != 0 {
		t.Fatalf("%d completed timers remain registered", remaining)
	}
}

func TestCloseDuringTimerRegistrationReleasesPendingWork(t *testing.T) {
	loop := newEventLoop()
	defer loop.close()
	var calls atomic.Int64
	var producers sync.WaitGroup
	start := make(chan struct{})
	for range 8 {
		producers.Add(1)
		go func() {
			defer producers.Done()
			<-start
			for range 1000 {
				loop.schedule(time.Hour, func() { calls.Add(1) })
			}
		}()
	}
	close(start)
	loop.close()
	producers.Wait()
	loop.schedule(time.Nanosecond, func() { calls.Add(1) })
	loop.mu.Lock()
	remaining, queued := len(loop.timers), len(loop.queue)
	loop.mu.Unlock()
	if remaining != 0 || queued != 0 || calls.Load() != 0 {
		t.Fatalf("closed loop retained work: timers=%d queue=%d callbacks=%d", remaining, queued, calls.Load())
	}
	if loop.post(func() { t.Error("closed loop accepted an immediate task") }) {
		t.Fatal("post succeeded after close")
	}
}
