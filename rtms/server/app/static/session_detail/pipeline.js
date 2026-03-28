(() => {
  const sessionDetail = (window.rtmsSessionDetail = window.rtmsSessionDetail || {});

  sessionDetail.initPipeline = (screen) => {
    if (!(screen instanceof HTMLElement)) {
      return;
    }

    const stages = Array.from(screen.querySelectorAll("[data-pipeline-stage]"));
    const navButtons = Array.from(screen.querySelectorAll("[data-pipeline-jump]"));
    const storageKey = screen.dataset.stageStorageKey || "";
    const activeStageStorageKey = screen.dataset.activeStageStorageKey || "";
    if (!stages.length) {
      return;
    }

    const setOpenStage = (targetStage) => {
      stages.forEach((stageElement) => {
        const isOpen = stageElement === targetStage;
        stageElement.classList.toggle("is-open", isOpen);
        const toggle = stageElement.querySelector("[data-stage-toggle]");
        if (toggle) {
          toggle.setAttribute("aria-expanded", String(isOpen));
        }
      });

      navButtons.forEach((button) => {
        const matches = button.getAttribute("data-pipeline-jump") === targetStage.id;
        button.classList.toggle("focused", matches);
      });

      if (storageKey) {
        window.sessionStorage.setItem(storageKey, targetStage.id);
      }
    };

    stages.forEach((stageElement) => {
      const toggle = stageElement.querySelector("[data-stage-toggle]");
      if (!toggle || toggle.dataset.stageBound === "true") {
        return;
      }
      toggle.dataset.stageBound = "true";
      toggle.addEventListener("click", () => {
        setOpenStage(stageElement);
      });
    });

    navButtons.forEach((button) => {
      if (button.dataset.pipelineBound === "true") {
        return;
      }
      button.dataset.pipelineBound = "true";
      button.addEventListener("click", () => {
        const targetId = button.getAttribute("data-pipeline-jump");
        const targetStage = targetId ? screen.querySelector(`#${targetId}`) : null;
        if (!(targetStage instanceof HTMLElement)) {
          return;
        }
        setOpenStage(targetStage);
        targetStage.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    });

    const currentActiveStage =
      stages.find((stageElement) => stageElement.dataset.stageStatus === "active") ||
      stages.find((stageElement) => stageElement.id === (screen.dataset.activeStageId || "")) ||
      null;
    const savedStageId = storageKey ? window.sessionStorage.getItem(storageKey) : "";
    const savedActiveStageId = activeStageStorageKey ? window.sessionStorage.getItem(activeStageStorageKey) : "";
    const savedStage = savedStageId
      ? stages.find((stageElement) => stageElement.id === savedStageId)
      : null;
    const shouldAdvanceToActiveStage =
      !!currentActiveStage &&
      currentActiveStage.id !== savedActiveStageId &&
      (
        !savedStage ||
        savedStageId === savedActiveStageId ||
        (!savedActiveStageId && savedStage.dataset.stageStatus === "complete")
      );
    const initialStage =
      (shouldAdvanceToActiveStage ? currentActiveStage : null) ||
      savedStage ||
      currentActiveStage ||
      stages.find((stageElement) => stageElement.classList.contains("is-open")) ||
      stages[0];
    setOpenStage(initialStage);
    screen.dataset.activeStageId = currentActiveStage ? currentActiveStage.id : initialStage.id;
    if (activeStageStorageKey && currentActiveStage) {
      window.sessionStorage.setItem(activeStageStorageKey, currentActiveStage.id);
    }
  };
})();
