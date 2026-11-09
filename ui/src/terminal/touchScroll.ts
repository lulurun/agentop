/**
 * A vertical finger drag on the terminal scrolls it, the way a mouse wheel
 * does on a desktop. xterm turns the wheel events into what tmux expects, so
 * the agent's history scrolls in tmux. A tap still focuses the terminal.
 */
const START_PX = 8; // below this, the touch is a tap

export function touchScroll(element: HTMLElement): () => void {
  let lastY: number | null = null;
  let scrolling = false;

  const start = (event: TouchEvent) => {
    lastY = event.touches.length === 1 ? event.touches[0].clientY : null;
    scrolling = false;
  };

  const move = (event: TouchEvent) => {
    if (lastY === null || event.touches.length !== 1) return;
    const touch = event.touches[0];
    const dy = touch.clientY - lastY;
    if (!scrolling && Math.abs(dy) < START_PX) return;
    scrolling = true;
    event.preventDefault();
    lastY = touch.clientY;
    const target = document.elementFromPoint(touch.clientX, touch.clientY) ?? element;
    target.dispatchEvent(
      new WheelEvent("wheel", {
        deltaY: -dy, // finger down shows earlier output, like wheel up
        deltaMode: WheelEvent.DOM_DELTA_PIXEL,
        clientX: touch.clientX,
        clientY: touch.clientY,
        bubbles: true,
        cancelable: true,
      }),
    );
  };

  const end = () => {
    lastY = null;
  };

  element.addEventListener("touchstart", start, { passive: true });
  element.addEventListener("touchmove", move, { passive: false });
  element.addEventListener("touchend", end);
  element.addEventListener("touchcancel", end);
  return () => {
    element.removeEventListener("touchstart", start);
    element.removeEventListener("touchmove", move);
    element.removeEventListener("touchend", end);
    element.removeEventListener("touchcancel", end);
  };
}
