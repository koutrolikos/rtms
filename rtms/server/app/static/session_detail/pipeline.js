(() => {
  const sessionDetail = (window.rtmsSessionDetail = window.rtmsSessionDetail || {});

  const readStoredStageId = (storageKey) => {
    if (!storageKey) {
      return "";
    }
    const raw = window.sessionStorage.getItem(storageKey) || "";
    if (!raw) {
      return "";
    }
    try {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) {
        return parsed.filter((value) => typeof value === "string" && value).at(-1) || "";
      }
      return typeof parsed === "string" ? parsed : raw;
    } catch (_error) {
      return raw;
    }
  };

  const writeStoredStageId = (storageKey, stageId) => {
    if (!storageKey) {
      return;
    }
    window.sessionStorage.setItem(storageKey, stageId);
  };

  sessionDetail.requestStage = (screen, stageId) => {
    if (!(screen instanceof HTMLElement) || !stageId) {
      return;
    }
    const storageKey = screen.dataset.stageStorageKey || "";
    writeStoredStageId(storageKey, stageId);
  };

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

    const requestStage = (stageId) => {
      sessionDetail.requestStage(screen, stageId);
    };

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
        writeStoredStageId(storageKey, targetStage.id);
      }
    };

    const currentActiveStage =
      stages.find((stageElement) => stageElement.dataset.stageStatus === "active") ||
      stages.find((stageElement) => stageElement.id === (screen.dataset.activeStageId || "")) ||
      null;
    const hasStageControls =
      navButtons.length > 0 ||
      stages.some((stageElement) => stageElement.querySelector("[data-stage-toggle]"));

    if (!hasStageControls) {
      stages.forEach((stageElement) => {
        stageElement.classList.add("is-open");
      });
      const activeStage = currentActiveStage || stages[0];
      screen.dataset.activeStageId = activeStage.id;
      if (activeStageStorageKey) {
        window.sessionStorage.setItem(activeStageStorageKey, activeStage.id);
      }
      return;
    }

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

    const stageForms = Array.from(screen.querySelectorAll("form[data-next-stage]"));
    stageForms.forEach((formElement) => {
      if (!(formElement instanceof HTMLFormElement) || formElement.dataset.stageAdvanceBound === "true") {
        return;
      }
      formElement.dataset.stageAdvanceBound = "true";
      formElement.addEventListener("submit", () => {
        const nextStageId = formElement.dataset.nextStage || "";
        requestStage(nextStageId);
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
    const savedStageId = readStoredStageId(storageKey);
    const savedActiveStageId = activeStageStorageKey ? window.sessionStorage.getItem(activeStageStorageKey) : "";
    const savedStage = savedStageId
      ? stages.find((stageElement) => stageElement.id === savedStageId)
      : null;
    const shouldAdvanceToActiveStage =
      !!currentActiveStage &&
      currentActiveStage.id !== savedActiveStageId &&
      (
        currentActiveStage.id === "stage-outputs" ||
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
