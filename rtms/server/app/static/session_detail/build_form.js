(() => {
  const sessionDetail = (window.rtmsSessionDetail = window.rtmsSessionDetail || {});

  sessionDetail.initBuildForm = (screen) => {
    if (!(screen instanceof HTMLElement)) {
      return;
    }

    const form = screen.querySelector("#session-build-form");
    if (!(form instanceof HTMLFormElement)) {
      return;
    }

    const buildStateKey = screen.dataset.buildStorageKey || "";
    const repoSelect = screen.querySelector("#build-repo-id");
    const commitQuery = screen.querySelector("#commit-query");
    const commitResults = screen.querySelector("#commit-results");
    const gitShaInput = screen.querySelector("#build-git-sha");
    const loadConfigButton = screen.querySelector("#load-build-config");
    const searchCommitsButton = screen.querySelector("#search-commits");
    const queueBuildButton = screen.querySelector("#queue-build-button");
    const statusText = screen.querySelector("#build-config-status");
    const summaryField = screen.querySelector("#build-config-summary");
    const buildConfigJson = screen.querySelector("#build-config-json");
    const buildRoleSelect = screen.querySelector("#build-role");
    const buildRoleHint = screen.querySelector("#build-role-hint");
    const machineLogStatPeriodMs = screen.querySelector("#build-machine-log-stat-period-ms");
    const hasTxBuild = form.dataset.hasTxBuild === "true";
    const hasRxBuild = form.dataset.hasRxBuild === "true";
    if (
      !(repoSelect instanceof HTMLSelectElement) ||
      !(commitQuery instanceof HTMLInputElement) ||
      !(commitResults instanceof HTMLElement) ||
      !(gitShaInput instanceof HTMLInputElement) ||
      !(loadConfigButton instanceof HTMLButtonElement) ||
      !(searchCommitsButton instanceof HTMLButtonElement) ||
      !(queueBuildButton instanceof HTMLButtonElement) ||
      !(statusText instanceof HTMLElement) ||
      !(summaryField instanceof HTMLElement) ||
      !(buildConfigJson instanceof HTMLInputElement) ||
      !(buildRoleSelect instanceof HTMLSelectElement) ||
      !(buildRoleHint instanceof HTMLElement) ||
      !(machineLogStatPeriodMs instanceof HTMLInputElement)
    ) {
      return;
    }

    const emptySummaryText = "Load a commit to inspect the resolved GitHub-build defaults.";
    const forcedMachineLogDetail = 1;
    let loadedConfigSha = "";
    let loadedBuildConfig = null;
    let loadedBuildConstraints = null;

    const readState = () => {
      if (!buildStateKey) {
        return null;
      }
      try {
        const raw = window.sessionStorage.getItem(buildStateKey);
        return raw ? JSON.parse(raw) : null;
      } catch (_error) {
        return null;
      }
    };

    const writeState = () => {
      if (!buildStateKey) {
        return;
      }
      try {
        window.sessionStorage.setItem(
          buildStateKey,
          JSON.stringify({
            repoId: repoSelect.value,
            commitQuery: commitQuery.value,
            gitSha: gitShaInput.value,
            buildHostId: form.elements.namedItem("build_host_id")?.value || "",
            role: buildRoleSelect.value,
            machineLogStatPeriodMs: machineLogStatPeriodMs.value,
            loadedConfigSha,
            loadedBuildConfig,
            loadedBuildConstraints,
            statusText: statusText.textContent || "",
            statusIsError: statusText.classList.contains("danger-text"),
            summaryText: summaryField.textContent || emptySummaryText,
            queueEnabled: !queueBuildButton.disabled,
          })
        );
      } catch (_error) {
        return;
      }
    };

    const clearState = () => {
      if (!buildStateKey) {
        return;
      }
      window.sessionStorage.removeItem(buildStateKey);
    };

    const setStatus = (message, isError = false) => {
      statusText.textContent = message;
      statusText.classList.toggle("danger-text", isError);
      writeState();
    };

    const fetchJson = async (url) => {
      const response = await fetch(url, { headers: { Accept: "application/json" } });
      const responseData = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(responseData.detail || `Request failed with status ${response.status}`);
      }
      return responseData;
    };

    const machineLogDetailLabel = (value) => (value === 1 ? "Packet" : "Summary");
    const normalizeBuildConfigForUi = (buildConfig) => ({
      ...buildConfig,
      machine_log_detail: forcedMachineLogDetail,
    });

    const updateBuildTargetUi = () => {
      const role = buildRoleSelect.value || "TX";
      queueBuildButton.textContent = `Queue ${role} Build`;
      const currentArtifact = role === "TX" ? buildRoleHint.dataset.txArtifact : buildRoleHint.dataset.rxArtifact;
      buildRoleHint.textContent = currentArtifact
        ? `Build auto-assigns to ${role} slot and replaces ${currentArtifact}.`
        : `Build auto-assigns to ${role} slot.`;
      writeState();
    };

    const renderSummary = (buildConfig, sha) => {
      summaryField.textContent = [
        `Git SHA: ${sha}`,
        "Human log: disabled",
        "Machine log: enabled",
        `Detail level: ${machineLogDetailLabel(buildConfig.machine_log_detail)}`,
        `Stat period: ${buildConfig.machine_log_stat_period_ms} ms`,
      ].join("\n");
      writeState();
    };

    const populateConfigForm = (payload) => {
      loadedBuildConfig = normalizeBuildConfigForUi(payload.build_config);
      loadedBuildConstraints = payload.constraints;
      machineLogStatPeriodMs.min = String(payload.constraints.machine_log_stat_period_ms_min ?? 0);
      machineLogStatPeriodMs.placeholder = String(loadedBuildConfig.machine_log_stat_period_ms);
      if (machineLogStatPeriodMs.value === "") {
        machineLogStatPeriodMs.value = "";
      }
      renderSummary(loadedBuildConfig, payload.git_sha);
    };

    const serializeBuildConfig = () => {
      if (!loadedBuildConfig) {
        throw new Error("Load config for this SHA first.");
      }
      const statPeriodValue = machineLogStatPeriodMs.value.trim();
      const effectiveStatPeriod =
        statPeriodValue === "" ? loadedBuildConfig.machine_log_stat_period_ms : Number(statPeriodValue);
      const minStatPeriod = loadedBuildConstraints?.machine_log_stat_period_ms_min ?? 0;
      if (!Number.isInteger(effectiveStatPeriod) || effectiveStatPeriod < minStatPeriod) {
        throw new Error(`Stat period must be at least ${minStatPeriod} ms.`);
      }
      return {
        machine_log_detail: forcedMachineLogDetail,
        machine_log_stat_period_ms: effectiveStatPeriod,
      };
    };

    const updateSummaryPreview = () => {
      if (!loadedBuildConfig) {
        return;
      }
      try {
        renderSummary(serializeBuildConfig(), loadedConfigSha || gitShaInput.value.trim());
      } catch (_error) {
        renderSummary(loadedBuildConfig, loadedConfigSha || gitShaInput.value.trim());
      }
    };

    const renderCommitResults = (commits) => {
      commitResults.innerHTML = "";
      if (!commits.length) {
        commitResults.innerHTML = '<p class="muted">No commits found.</p>';
        writeState();
        return;
      }
      for (const commit of commits) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "commit-choice";
        button.innerHTML = `
          <strong>${commit.short_sha}</strong>
          <span>${commit.message}</span>
          <small>${commit.author_name || "unknown author"} | ${commit.author_date || "-"}</small>
        `;
        button.addEventListener("click", async () => {
          for (const choice of commitResults.querySelectorAll(".commit-choice")) {
            if (choice !== button) {
              choice.remove();
            }
          }
          button.classList.add("is-selected");
          button.disabled = true;
          gitShaInput.value = commit.sha;
          commitQuery.value = commit.short_sha;
          writeState();
          await loadBuildConfig();
        });
        commitResults.appendChild(button);
      }
      writeState();
    };

    const searchCommits = async () => {
      if (!repoSelect.value) {
        setStatus("Select a repo first.", true);
        return;
      }
      setStatus("Loading commits...");
      commitResults.innerHTML = "";
      try {
        const query = commitQuery.value.trim();
        const suffix = query ? `?q=${encodeURIComponent(query)}` : "";
        const commits = await fetchJson(`/api/repos/${encodeURIComponent(repoSelect.value)}/commits${suffix}`);
        renderCommitResults(commits);
        setStatus("Select a commit or enter a SHA.");
      } catch (error) {
        setStatus(error.message, true);
      }
    };

    const loadBuildConfig = async () => {
      if (!repoSelect.value || !gitShaInput.value.trim()) {
        setStatus("Choose a repo and enter a SHA.", true);
        return;
      }
      queueBuildButton.disabled = true;
      setStatus("Loading config...");
      try {
        const payload = await fetchJson(
          `/api/repos/${encodeURIComponent(repoSelect.value)}/build-config?git_sha=${encodeURIComponent(gitShaInput.value.trim())}`
        );
        gitShaInput.value = payload.git_sha;
        loadedConfigSha = payload.git_sha;
        populateConfigForm(payload);
        queueBuildButton.disabled = false;
        setStatus(`Loaded ${payload.git_sha}. Packet detail will be used for this build.`);
      } catch (error) {
        loadedConfigSha = "";
        loadedBuildConfig = null;
        loadedBuildConstraints = null;
        summaryField.textContent = emptySummaryText;
        setStatus(error.message, true);
      }
      writeState();
    };

    const restoreState = () => {
      const state = readState();
      if (!state) {
        updateBuildTargetUi();
        return;
      }
      repoSelect.value = state.repoId || repoSelect.value;
      commitQuery.value = state.commitQuery || "";
      gitShaInput.value = state.gitSha || "";
      buildRoleSelect.value = state.role || buildRoleSelect.value;
      machineLogStatPeriodMs.value = state.machineLogStatPeriodMs || "";
      const buildHostSelect = form.elements.namedItem("build_host_id");
      if (buildHostSelect instanceof HTMLSelectElement && state.buildHostId) {
        buildHostSelect.value = state.buildHostId;
      }
      loadedConfigSha = state.loadedConfigSha || "";
      loadedBuildConfig = state.loadedBuildConfig || null;
      loadedBuildConstraints = state.loadedBuildConstraints || null;
      statusText.textContent = state.statusText || "Load a commit before queueing the build.";
      statusText.classList.toggle("danger-text", Boolean(state.statusIsError));
      summaryField.textContent = state.summaryText || emptySummaryText;
      if (loadedBuildConfig && loadedConfigSha && loadedConfigSha === gitShaInput.value.trim()) {
        queueBuildButton.disabled = !state.queueEnabled;
      } else {
        queueBuildButton.disabled = true;
      }
      updateBuildTargetUi();
    };

    if (form.dataset.buildBound !== "true") {
      form.dataset.buildBound = "true";
      form.addEventListener("submit", (event) => {
        try {
          if (loadedConfigSha !== gitShaInput.value.trim()) {
            throw new Error("Load config for this SHA first.");
          }
          const payload = serializeBuildConfig();
          const selectedRole = buildRoleSelect.value || "TX";
          const willHaveTxBuild = hasTxBuild || selectedRole === "TX";
          const willHaveRxBuild = hasRxBuild || selectedRole === "RX";
          buildConfigJson.value = JSON.stringify(payload);
          renderSummary(payload, gitShaInput.value.trim());
          if (willHaveTxBuild && willHaveRxBuild) {
            sessionDetail.requestStage?.(screen, "stage-run");
          }
          clearState();
        } catch (error) {
          event.preventDefault();
          setStatus(error.message, true);
        }
      });
    }

    if (repoSelect.dataset.buildBound !== "true") {
      repoSelect.dataset.buildBound = "true";
      repoSelect.addEventListener("change", () => {
        loadedConfigSha = "";
        loadedBuildConfig = null;
        loadedBuildConstraints = null;
        queueBuildButton.disabled = true;
        commitResults.innerHTML = "";
        summaryField.textContent = emptySummaryText;
        setStatus("Load a commit before queueing the build.");
        writeState();
      });
    }

    if (gitShaInput.dataset.buildBound !== "true") {
      gitShaInput.dataset.buildBound = "true";
      gitShaInput.addEventListener("input", () => {
        if (gitShaInput.value.trim() !== loadedConfigSha) {
          queueBuildButton.disabled = true;
        }
        writeState();
      });
    }

    if (buildRoleSelect.dataset.buildBound !== "true") {
      buildRoleSelect.dataset.buildBound = "true";
      buildRoleSelect.addEventListener("change", updateBuildTargetUi);
    }
    if (machineLogStatPeriodMs.dataset.buildBound !== "true") {
      machineLogStatPeriodMs.dataset.buildBound = "true";
      machineLogStatPeriodMs.addEventListener("input", updateSummaryPreview);
      machineLogStatPeriodMs.addEventListener("change", writeState);
    }
    if (commitQuery.dataset.buildBound !== "true") {
      commitQuery.dataset.buildBound = "true";
      commitQuery.addEventListener("input", writeState);
    }
    if (searchCommitsButton.dataset.buildBound !== "true") {
      searchCommitsButton.dataset.buildBound = "true";
      searchCommitsButton.addEventListener("click", searchCommits);
    }
    if (loadConfigButton.dataset.buildBound !== "true") {
      loadConfigButton.dataset.buildBound = "true";
      loadConfigButton.addEventListener("click", loadBuildConfig);
    }
    const buildHostSelect = form.elements.namedItem("build_host_id");
    if (buildHostSelect instanceof HTMLSelectElement && buildHostSelect.dataset.buildBound !== "true") {
      buildHostSelect.dataset.buildBound = "true";
      buildHostSelect.addEventListener("change", writeState);
    }

    restoreState();
  };
})();
