import { useCallback, useEffect, useRef, useState } from 'react';

const DEFAULT_THRESHOLD = 112;

function prefersReducedMotion() {
  return typeof window !== 'undefined'
    && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
}

export default function usePinnedScroll({ resetKey, contentKey, threshold = DEFAULT_THRESHOLD }) {
  const viewportRef = useRef(null);
  const contentRef = useRef(null);
  const pinnedRef = useRef(true);
  const frameRef = useRef(0);
  const [pinned, setPinned] = useState(true);
  const [hasNewContent, setHasNewContent] = useState(false);

  const updatePinnedState = useCallback(() => {
    const viewport = viewportRef.current;
    if (!viewport) return true;
    const distance = viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight;
    const nextPinned = distance <= threshold;
    pinnedRef.current = nextPinned;
    setPinned((current) => (current === nextPinned ? current : nextPinned));
    if (nextPinned) setHasNewContent(false);
    return nextPinned;
  }, [threshold]);

  const scrollToBottom = useCallback((behavior = 'auto') => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    window.cancelAnimationFrame(frameRef.current);
    frameRef.current = window.requestAnimationFrame(() => {
      viewport.scrollTo({
        top: viewport.scrollHeight,
        behavior: prefersReducedMotion() ? 'auto' : behavior,
      });
      pinnedRef.current = true;
      setPinned(true);
      setHasNewContent(false);
    });
  }, []);

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return undefined;
    const onScroll = () => updatePinnedState();
    viewport.addEventListener('scroll', onScroll, { passive: true });
    updatePinnedState();
    return () => viewport.removeEventListener('scroll', onScroll);
  }, [updatePinnedState]);

  useEffect(() => {
    scrollToBottom('auto');
  }, [resetKey, scrollToBottom]);

  useEffect(() => {
    if (pinnedRef.current) scrollToBottom('auto');
    else setHasNewContent(true);
  }, [contentKey, scrollToBottom]);

  useEffect(() => {
    const content = contentRef.current;
    if (!content || typeof ResizeObserver === 'undefined') return undefined;
    const observer = new ResizeObserver(() => {
      if (pinnedRef.current) scrollToBottom('auto');
      else setHasNewContent(true);
    });
    observer.observe(content);
    return () => observer.disconnect();
  }, [resetKey, scrollToBottom]);

  useEffect(() => () => window.cancelAnimationFrame(frameRef.current), []);

  return {
    viewportRef,
    contentRef,
    pinned,
    hasNewContent,
    scrollToBottom,
    updatePinnedState,
  };
}
