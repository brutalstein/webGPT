import { useEffect, useRef } from 'react';

const modalStack = [];
let bodyLockCount = 0;

const FOCUSABLE = [
  'button:not([disabled])',
  'a[href]',
  'input:not([disabled])',
  'textarea:not([disabled])',
  'select:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

export default function useDialogFocus({ open, onEscape, initialFocusRef }) {
  const dialogRef = useRef(null);
  const instanceRef = useRef(Symbol('os-dialog'));
  const escapeRef = useRef(onEscape);
  escapeRef.current = onEscape;

  useEffect(() => {
    if (!open) return undefined;
    const previousFocus = document.activeElement;
    const dialog = dialogRef.current;
    const instance = instanceRef.current;
    modalStack.push(instance);
    bodyLockCount += 1;
    document.body.classList.add('modal-open');

    const focusInitial = () => {
      const target = initialFocusRef?.current || dialog?.querySelector(FOCUSABLE);
      target?.focus({ preventScroll: true });
    };
    const frame = window.requestAnimationFrame(focusInitial);

    const onKeyDown = (event) => {
      if (modalStack.at(-1) !== instance) return;
      if (event.key === 'Escape') {
        event.preventDefault();
        escapeRef.current?.();
        return;
      }
      if (event.key !== 'Tab' || !dialog) return;
      const focusable = [...dialog.querySelectorAll(FOCUSABLE)]
        .filter((element) => !element.hidden && element.getClientRects().length > 0);
      if (focusable.length === 0) {
        event.preventDefault();
        dialog.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener('keydown', onKeyDown, true);
    return () => {
      window.cancelAnimationFrame(frame);
      document.removeEventListener('keydown', onKeyDown, true);
      const index = modalStack.lastIndexOf(instance);
      if (index >= 0) modalStack.splice(index, 1);
      bodyLockCount = Math.max(0, bodyLockCount - 1);
      if (bodyLockCount === 0) document.body.classList.remove('modal-open');
      if (previousFocus instanceof HTMLElement && previousFocus.isConnected && modalStack.length === 0) {
        previousFocus.focus({ preventScroll: true });
      }
    };
  }, [open, initialFocusRef]);

  return dialogRef;
}
