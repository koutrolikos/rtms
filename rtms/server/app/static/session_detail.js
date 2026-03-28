(() => {
  const initSessionScreen = (screen = document.querySelector(".session-screen")) => {
    if (!(screen instanceof HTMLElement)) {
      return;
    }

    window.rtmsSessionDetail?.initPipeline?.(screen);
    window.rtmsSessionDetail?.initBuildForm?.(screen);
  };

  window.rtmsInitSessionScreen = initSessionScreen;
  initSessionScreen();
})();
