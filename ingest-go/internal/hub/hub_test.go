package hub

import (
	"fmt"
	"sync"
	"testing"
)

func TestPublishFansOutToAllSubscribers(t *testing.T) {
	h := New("boot", 10, 10)
	a, _ := h.Subscribe("")
	b, _ := h.Subscribe("")
	h.Publish([]byte("x"))

	for name, s := range map[string]*Subscriber{"a": a, "b": b} {
		m := <-s.Messages()
		if string(m.Data) != "x" || m.Seq != 1 {
			t.Errorf("subscriber %s got %+v", name, m)
		}
	}
}

func TestSubscribeReplaysMissedMessagesForOwnEventID(t *testing.T) {
	h := New("boot", 10, 10)
	for i := 1; i <= 5; i++ {
		h.Publish([]byte(fmt.Sprint(i)))
	}
	_, backlog := h.Subscribe(h.EventID(3))
	if len(backlog) != 2 || string(backlog[0].Data) != "4" || string(backlog[1].Data) != "5" {
		t.Fatalf("want messages 4,5; got %+v", backlog)
	}
	if got := h.Stats().ReplayedMessages; got != 2 {
		t.Errorf("replayed counter = %d", got)
	}
}

func TestSubscribeIgnoresForeignOrUpToDateIDs(t *testing.T) {
	h := New("boot", 10, 10)
	h.Publish([]byte("1"))
	for _, id := range []string{"", "otherboot-0", `[{"topic":"x"}]`, "boot-notanumber", h.EventID(1)} {
		if _, backlog := h.Subscribe(id); len(backlog) != 0 {
			t.Errorf("id %q: want no backlog, got %d", id, len(backlog))
		}
	}
}

func TestRingBufferKeepsOnlyNewestMessages(t *testing.T) {
	h := New("boot", 3, 10)
	for i := 1; i <= 6; i++ {
		h.Publish([]byte(fmt.Sprint(i)))
	}
	_, backlog := h.Subscribe(h.EventID(1)) // 2..6 requested, only 4..6 retained
	if len(backlog) != 3 || string(backlog[0].Data) != "4" || string(backlog[2].Data) != "6" {
		t.Fatalf("unexpected backlog: %+v", backlog)
	}
}

func TestSlowSubscriberIsDisconnectedWithoutBlockingPublisher(t *testing.T) {
	h := New("boot", 10, 2)
	slow, _ := h.Subscribe("")
	fast, _ := h.Subscribe("")

	h.Publish([]byte("1"))
	h.Publish([]byte("2")) // both buffers now full
	for i := 0; i < 2; i++ {
		<-fast.Messages() // fast keeps up, slow does not read
	}
	h.Publish([]byte("3")) // must not block: slow is disconnected instead

	if m := <-fast.Messages(); string(m.Data) != "3" {
		t.Fatalf("fast subscriber should receive 3, got %q", m.Data)
	}
	n := 0
	for range slow.Messages() { // drains its 2 buffered messages, then sees the close
		n++
	}
	if n != 2 {
		t.Errorf("slow subscriber should keep its 2 buffered messages, got %d", n)
	}
	if s := h.Stats(); s.SlowDisconnects != 1 || s.Subscribers != 1 {
		t.Errorf("stats wrong: %+v", s)
	}
	h.Unsubscribe(fast)
}

func TestUnsubscribeIsIdempotent(t *testing.T) {
	h := New("boot", 1, 1)
	s, _ := h.Subscribe("")
	h.Unsubscribe(s)
	h.Unsubscribe(s) // must not panic on double close
	if _, ok := <-s.Messages(); ok {
		t.Fatal("channel should be closed")
	}
	if h.Stats().Subscribers != 0 {
		t.Fatal("subscriber not removed")
	}
}

func TestConcurrentPublishAndSubscribe(t *testing.T) {
	h := New("boot", 64, 1024)
	var wg sync.WaitGroup
	for p := 0; p < 4; p++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for i := 0; i < 200; i++ {
				h.Publish([]byte("m"))
			}
		}()
	}
	for c := 0; c < 4; c++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			s, _ := h.Subscribe("")
			h.Unsubscribe(s)
		}()
	}
	wg.Wait()
	if got := h.Stats().Published; got != 800 {
		t.Fatalf("published = %d, want 800", got)
	}
}
