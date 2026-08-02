/** Shared framer-motion presets for GLOF Portal */

export const easeOut = [0.22, 1, 0.36, 1];
export const springSoft = { type: 'spring', stiffness: 280, damping: 26, mass: 0.9 };
export const springSnappy = { type: 'spring', stiffness: 420, damping: 28 };

export const pageTransition = {
  initial: { opacity: 0, y: 18, filter: 'blur(4px)' },
  animate: {
    opacity: 1,
    y: 0,
    filter: 'blur(0px)',
    transition: { duration: 0.38, ease: easeOut },
  },
  exit: {
    opacity: 0,
    y: -12,
    filter: 'blur(3px)',
    transition: { duration: 0.22, ease: easeOut },
  },
};

export const fadeUp = {
  hidden: { opacity: 0, y: 22 },
  show: {
    opacity: 1,
    y: 0,
    transition: { duration: 0.45, ease: easeOut },
  },
};

export const fadeIn = {
  hidden: { opacity: 0 },
  show: { opacity: 1, transition: { duration: 0.4, ease: easeOut } },
};

export const scaleIn = {
  hidden: { opacity: 0, scale: 0.94 },
  show: {
    opacity: 1,
    scale: 1,
    transition: springSoft,
  },
};

export const staggerContainer = {
  hidden: { opacity: 0 },
  show: {
    opacity: 1,
    transition: {
      staggerChildren: 0.07,
      delayChildren: 0.05,
    },
  },
};

export const staggerFast = {
  hidden: { opacity: 0 },
  show: {
    opacity: 1,
    transition: {
      staggerChildren: 0.045,
      delayChildren: 0.02,
    },
  },
};

export const cardItem = {
  hidden: { opacity: 0, y: 18, scale: 0.97 },
  show: {
    opacity: 1,
    y: 0,
    scale: 1,
    transition: springSoft,
  },
};

export const slideFromLeft = {
  hidden: { opacity: 0, x: -28 },
  show: {
    opacity: 1,
    x: 0,
    transition: { duration: 0.5, ease: easeOut },
  },
};

export const modalBackdrop = {
  hidden: { opacity: 0 },
  show: { opacity: 1, transition: { duration: 0.22 } },
  exit: { opacity: 0, transition: { duration: 0.18 } },
};

export const modalPanel = {
  hidden: { opacity: 0, y: 36, scale: 0.96 },
  show: {
    opacity: 1,
    y: 0,
    scale: 1,
    transition: springSoft,
  },
  exit: {
    opacity: 0,
    y: 18,
    scale: 0.98,
    transition: { duration: 0.18, ease: easeOut },
  },
};

export const hoverLift = {
  rest: { y: 0, scale: 1 },
  hover: {
    y: -4,
    scale: 1.015,
    transition: { duration: 0.2, ease: easeOut },
  },
  tap: { scale: 0.98, y: 0 },
};

export const navHover = {
  rest: { x: 0 },
  hover: { x: 3 },
  tap: { scale: 0.98 },
};
