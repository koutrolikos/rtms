(() => {
  const sessionDetail = (window.rtmsSessionDetail = window.rtmsSessionDetail || {});

  const requiredElement = (screen, selector, type) => {
    const element = screen.querySelector(selector);
    return element instanceof type ? element : null;
  };

  const setInputValue = (input, value) => {
    input.value = value == null ? "" : String(value);
  };

  const parseInteger = (rawValue) => {
    const value = rawValue.trim();
    if (!value) {
      return null;
    }
    const parsed = /^0x/i.test(value) ? Number.parseInt(value, 16) : Number(value);
    return Number.isInteger(parsed) ? parsed : null;
  };

  const formatHex = (value, width = 4) => {
    const safeValue = Number.isInteger(value) ? value : 0;
    return `0x${safeValue.toString(16).toUpperCase().padStart(width, "0")}`;
  };

  sessionDetail.initBuildForm = (screen) => {
    if (!(screen instanceof HTMLElement)) {
      return;
    }

    const form = requiredElement(screen, "#session-build-form", HTMLFormElement);
    if (!form) {
      return;
    }

    const buildStateKey = screen.dataset.buildStorageKey || "";
    const repoSelect = requiredElement(screen, "#build-repo-id", HTMLSelectElement);
    const commitQuery = requiredElement(screen, "#commit-query", HTMLInputElement);
    const commitResults = requiredElement(screen, "#commit-results", HTMLElement);
    const gitShaInput = requiredElement(screen, "#build-git-sha", HTMLInputElement);
    const loadConfigButton = requiredElement(screen, "#load-build-config", HTMLButtonElement);
    const searchCommitsButton = requiredElement(screen, "#search-commits", HTMLButtonElement);
    const queueBuildButton = requiredElement(screen, "#queue-build-button", HTMLButtonElement);
    const statusText = requiredElement(screen, "#build-config-status", HTMLElement);
    const summaryField = requiredElement(screen, "#build-config-summary", HTMLElement);
    const buildConfigJson = requiredElement(screen, "#build-config-json", HTMLInputElement);
    const buildRoleSelect = requiredElement(screen, "#build-role", HTMLSelectElement);
    const buildRoleHint = requiredElement(screen, "#build-role-hint", HTMLElement);
    const machineLogStatPeriodMs = requiredElement(screen, "#build-machine-log-stat-period-ms", HTMLInputElement);
    const rfBitrateBps = requiredElement(screen, "#build-rf-bitrate-bps", HTMLInputElement);
    const rfRxBwHz = requiredElement(screen, "#build-rf-rx-bw-hz", HTMLInputElement);
    const rfDeviationHz = requiredElement(screen, "#build-rf-deviation-hz", HTMLInputElement);
    const rfPreambleBytes = requiredElement(screen, "#build-rf-preamble-bytes", HTMLSelectElement);
    const rfSyncWord = requiredElement(screen, "#build-rf-sync-word", HTMLInputElement);
    const allowlist0 = requiredElement(screen, "#build-allowlist-0", HTMLInputElement);
    const allowlist1 = requiredElement(screen, "#build-allowlist-1", HTMLInputElement);
    const bandMinHz = requiredElement(screen, "#build-band-min-hz", HTMLInputElement);
    const bandMaxHz = requiredElement(screen, "#build-band-max-hz", HTMLInputElement);
    const guardBandHz = requiredElement(screen, "#build-guard-band-hz", HTMLInputElement);
    const backupFailoverHoldoffMs = requiredElement(
      screen,
      "#build-backup-failover-holdoff-ms",
      HTMLInputElement
    );
    const rxThreshEnable = requiredElement(screen, "#build-rx-thresh-enable", HTMLSelectElement);
    const rxMinRssiDbm = requiredElement(screen, "#build-rx-min-rssi-dbm", HTMLInputElement);
    const rxMinLqi = requiredElement(screen, "#build-rx-min-lqi", HTMLInputElement);
    const rxThreshLogEvery = requiredElement(screen, "#build-rx-thresh-log-every", HTMLInputElement);
    const rxPollIntervalMs = requiredElement(screen, "#build-rx-poll-interval-ms", HTMLInputElement);
    const txCompleteTimeoutMs = requiredElement(screen, "#build-tx-complete-timeout-ms", HTMLInputElement);
    const rxHostBridgeBudget = requiredElement(screen, "#build-rx-host-bridge-budget", HTMLInputElement);
    const telemGpsPeriodMs = requiredElement(screen, "#build-telem-gps-period-ms", HTMLInputElement);
    const telemImuBaroPeriodMs = requiredElement(screen, "#build-telem-imu-baro-period-ms", HTMLInputElement);
    const airtimeLimitUsPerHour = requiredElement(
      screen,
      "#build-airtime-limit-us-per-hour",
      HTMLInputElement
    );
    const paTableInputs = Array.from({ length: 8 }, (_, index) =>
      requiredElement(screen, `#build-rf-pa-table-${index}`, HTMLInputElement)
    );
    const exclusionMaskInputs = Array.from({ length: 4 }, (_, index) => ({
      center: requiredElement(screen, `#build-exclusion-mask-${index}-center-hz`, HTMLInputElement),
      halfBw: requiredElement(screen, `#build-exclusion-mask-${index}-half-bw-hz`, HTMLInputElement),
    }));
    const hasTxBuild = form.dataset.hasTxBuild === "true";
    const hasRxBuild = form.dataset.hasRxBuild === "true";

    if (
      !repoSelect ||
      !commitQuery ||
      !commitResults ||
      !gitShaInput ||
      !loadConfigButton ||
      !searchCommitsButton ||
      !queueBuildButton ||
      !statusText ||
      !summaryField ||
      !buildConfigJson ||
      !buildRoleSelect ||
      !buildRoleHint ||
      !machineLogStatPeriodMs ||
      !rfBitrateBps ||
      !rfRxBwHz ||
      !rfDeviationHz ||
      !rfPreambleBytes ||
      !rfSyncWord ||
      !allowlist0 ||
      !allowlist1 ||
      !bandMinHz ||
      !bandMaxHz ||
      !guardBandHz ||
      !backupFailoverHoldoffMs ||
      !rxThreshEnable ||
      !rxMinRssiDbm ||
      !rxMinLqi ||
      !rxThreshLogEvery ||
      !rxPollIntervalMs ||
      !txCompleteTimeoutMs ||
      !rxHostBridgeBudget ||
      !telemGpsPeriodMs ||
      !telemImuBaroPeriodMs ||
      !airtimeLimitUsPerHour ||
      paTableInputs.some((input) => !(input instanceof HTMLInputElement)) ||
      exclusionMaskInputs.some(
        (row) => !(row.center instanceof HTMLInputElement) || !(row.halfBw instanceof HTMLInputElement)
      )
    ) {
      return;
    }

    const emptySummaryText = "Load a commit to inspect the resolved GitHub-build defaults.";
    const forcedMachineLogDetail = 1;
    let loadedConfigSha = "";
    let loadedBuildConfig = null;
    let loadedBuildConstraints = null;

    const configInputs = [
      rfBitrateBps,
      rfRxBwHz,
      rfDeviationHz,
      rfPreambleBytes,
      rfSyncWord,
      allowlist0,
      allowlist1,
      bandMinHz,
      bandMaxHz,
      guardBandHz,
      backupFailoverHoldoffMs,
      rxThreshEnable,
      rxMinRssiDbm,
      rxMinLqi,
      rxThreshLogEvery,
      rxPollIntervalMs,
      txCompleteTimeoutMs,
      rxHostBridgeBudget,
      telemGpsPeriodMs,
      telemImuBaroPeriodMs,
      airtimeLimitUsPerHour,
      ...paTableInputs,
      ...exclusionMaskInputs.flatMap((row) => [row.center, row.halfBw]),
    ];

    const captureFieldValues = () => ({
      machineLogStatPeriodMs: machineLogStatPeriodMs.value,
      rfBitrateBps: rfBitrateBps.value,
      rfRxBwHz: rfRxBwHz.value,
      rfDeviationHz: rfDeviationHz.value,
      rfPreambleBytes: rfPreambleBytes.value,
      rfSyncWord: rfSyncWord.value,
      allowlist0: allowlist0.value,
      allowlist1: allowlist1.value,
      bandMinHz: bandMinHz.value,
      bandMaxHz: bandMaxHz.value,
      guardBandHz: guardBandHz.value,
      backupFailoverHoldoffMs: backupFailoverHoldoffMs.value,
      rxThreshEnable: rxThreshEnable.value,
      rxMinRssiDbm: rxMinRssiDbm.value,
      rxMinLqi: rxMinLqi.value,
      rxThreshLogEvery: rxThreshLogEvery.value,
      rxPollIntervalMs: rxPollIntervalMs.value,
      txCompleteTimeoutMs: txCompleteTimeoutMs.value,
      rxHostBridgeBudget: rxHostBridgeBudget.value,
      telemGpsPeriodMs: telemGpsPeriodMs.value,
      telemImuBaroPeriodMs: telemImuBaroPeriodMs.value,
      airtimeLimitUsPerHour: airtimeLimitUsPerHour.value,
      paTable: paTableInputs.map((input) => input.value),
      exclusionMasks: exclusionMaskInputs.map((row) => ({
        center: row.center.value,
        halfBw: row.halfBw.value,
      })),
    });

    const applyFieldValues = (fieldValues = {}) => {
      setInputValue(machineLogStatPeriodMs, fieldValues.machineLogStatPeriodMs);
      setInputValue(rfBitrateBps, fieldValues.rfBitrateBps);
      setInputValue(rfRxBwHz, fieldValues.rfRxBwHz);
      setInputValue(rfDeviationHz, fieldValues.rfDeviationHz);
      setInputValue(rfPreambleBytes, fieldValues.rfPreambleBytes);
      setInputValue(rfSyncWord, fieldValues.rfSyncWord);
      setInputValue(allowlist0, fieldValues.allowlist0);
      setInputValue(allowlist1, fieldValues.allowlist1);
      setInputValue(bandMinHz, fieldValues.bandMinHz);
      setInputValue(bandMaxHz, fieldValues.bandMaxHz);
      setInputValue(guardBandHz, fieldValues.guardBandHz);
      setInputValue(backupFailoverHoldoffMs, fieldValues.backupFailoverHoldoffMs);
      setInputValue(rxThreshEnable, fieldValues.rxThreshEnable);
      setInputValue(rxMinRssiDbm, fieldValues.rxMinRssiDbm);
      setInputValue(rxMinLqi, fieldValues.rxMinLqi);
      setInputValue(rxThreshLogEvery, fieldValues.rxThreshLogEvery);
      setInputValue(rxPollIntervalMs, fieldValues.rxPollIntervalMs);
      setInputValue(txCompleteTimeoutMs, fieldValues.txCompleteTimeoutMs);
      setInputValue(rxHostBridgeBudget, fieldValues.rxHostBridgeBudget);
      setInputValue(telemGpsPeriodMs, fieldValues.telemGpsPeriodMs);
      setInputValue(telemImuBaroPeriodMs, fieldValues.telemImuBaroPeriodMs);
      setInputValue(airtimeLimitUsPerHour, fieldValues.airtimeLimitUsPerHour);
      paTableInputs.forEach((input, index) => setInputValue(input, fieldValues.paTable?.[index]));
      exclusionMaskInputs.forEach((row, index) => {
        setInputValue(row.center, fieldValues.exclusionMasks?.[index]?.center);
        setInputValue(row.halfBw, fieldValues.exclusionMasks?.[index]?.halfBw);
      });
    };

    const clearConfigForm = () => {
      applyFieldValues({
        machineLogStatPeriodMs: "",
        rfPreambleBytes: "4",
        rxThreshEnable: "1",
        paTable: Array(8).fill(""),
        exclusionMasks: Array.from({ length: 4 }, () => ({ center: "", halfBw: "" })),
      });
    };

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
            fieldValues: captureFieldValues(),
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

    const normalizeBuildConfigForUi = (buildConfig) => ({
      ...buildConfig,
      machine_log_detail: forcedMachineLogDetail,
    });

    const applyRange = (input, range) => {
      if (!range || typeof range !== "object") {
        return;
      }
      if (Number.isInteger(range.min)) {
        input.min = String(range.min);
      }
      if (Number.isInteger(range.max)) {
        input.max = String(range.max);
      }
    };

    const applyConstraints = (constraints) => {
      applyRange(machineLogStatPeriodMs, constraints.machine_log_stat_period_ms);
      applyRange(rfBitrateBps, constraints.rf_bitrate_bps);
      applyRange(rfRxBwHz, constraints.rf_rx_bw_hz);
      applyRange(rfDeviationHz, constraints.rf_deviation_hz);
      applyRange(bandMinHz, constraints.band_min_hz);
      applyRange(bandMaxHz, constraints.band_max_hz);
      applyRange(guardBandHz, constraints.guard_band_hz);
      applyRange(backupFailoverHoldoffMs, constraints.backup_failover_holdoff_ms);
      applyRange(rxMinRssiDbm, constraints.rx_min_rssi_dbm);
      applyRange(rxMinLqi, constraints.rx_min_lqi);
      applyRange(rxThreshLogEvery, constraints.rx_thresh_log_every);
      applyRange(rxPollIntervalMs, constraints.rx_poll_interval_ms);
      applyRange(txCompleteTimeoutMs, constraints.tx_complete_timeout_ms);
      applyRange(rxHostBridgeBudget, constraints.rx_host_bridge_budget);
      applyRange(telemGpsPeriodMs, constraints.telem_gps_period_ms);
      applyRange(telemImuBaroPeriodMs, constraints.telem_imu_baro_period_ms);
      applyRange(airtimeLimitUsPerHour, constraints.airtime_limit_us_per_hour);
      paTableInputs.forEach((input) => applyRange(input, constraints.rf_pa_table_value));
      exclusionMaskInputs.forEach((row) => {
        applyRange(row.center, constraints.exclusion_mask_center_hz);
        applyRange(row.halfBw, constraints.exclusion_mask_half_bw_hz);
      });
    };

    const applyBuildConfigToForm = (buildConfig) => {
      setInputValue(machineLogStatPeriodMs, buildConfig.machine_log_stat_period_ms);
      setInputValue(rfBitrateBps, buildConfig.rf_bitrate_bps);
      setInputValue(rfRxBwHz, buildConfig.rf_rx_bw_hz);
      setInputValue(rfDeviationHz, buildConfig.rf_deviation_hz);
      setInputValue(rfPreambleBytes, buildConfig.rf_preamble_bytes);
      setInputValue(rfSyncWord, formatHex(buildConfig.rf_sync_word));
      paTableInputs.forEach((input, index) => setInputValue(input, buildConfig.rf_pa_table?.[index]));
      setInputValue(allowlist0, buildConfig.allowlist_hz?.[0]);
      setInputValue(allowlist1, buildConfig.allowlist_hz?.[1]);
      setInputValue(bandMinHz, buildConfig.band_min_hz);
      setInputValue(bandMaxHz, buildConfig.band_max_hz);
      setInputValue(guardBandHz, buildConfig.guard_band_hz);
      setInputValue(backupFailoverHoldoffMs, buildConfig.backup_failover_holdoff_ms);
      exclusionMaskInputs.forEach((row, index) => {
        const mask = buildConfig.exclusion_masks?.[index];
        setInputValue(row.center, mask?.center_hz);
        setInputValue(row.halfBw, mask?.half_bw_hz);
      });
      setInputValue(rxThreshEnable, buildConfig.rx_thresh_enable);
      setInputValue(rxMinRssiDbm, buildConfig.rx_min_rssi_dbm);
      setInputValue(rxMinLqi, buildConfig.rx_min_lqi);
      setInputValue(rxThreshLogEvery, buildConfig.rx_thresh_log_every);
      setInputValue(rxPollIntervalMs, buildConfig.rx_poll_interval_ms);
      setInputValue(txCompleteTimeoutMs, buildConfig.tx_complete_timeout_ms);
      setInputValue(rxHostBridgeBudget, buildConfig.rx_host_bridge_budget);
      setInputValue(telemGpsPeriodMs, buildConfig.telem_gps_period_ms);
      setInputValue(telemImuBaroPeriodMs, buildConfig.telem_imu_baro_period_ms);
      setInputValue(airtimeLimitUsPerHour, buildConfig.airtime_limit_us_per_hour);
    };

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
      const paTable = (buildConfig.rf_pa_table || []).map((value) => formatHex(value, 2)).join(", ");
      const masks = buildConfig.exclusion_masks || [];
      summaryField.textContent = [
        `Git SHA: ${sha}`,
        `Logs: human disabled | machine enabled | detail Packet | stat period ${buildConfig.machine_log_stat_period_ms} ms`,
        `Radio: bitrate ${buildConfig.rf_bitrate_bps} bps | RX BW ${buildConfig.rf_rx_bw_hz} Hz | deviation ${buildConfig.rf_deviation_hz} Hz | preamble ${buildConfig.rf_preamble_bytes} B | sync ${formatHex(buildConfig.rf_sync_word)}`,
        `PA Table: ${paTable}`,
        `Channels: ${buildConfig.allowlist_hz.join(", ")} Hz | band ${buildConfig.band_min_hz}-${buildConfig.band_max_hz} Hz | guard ${buildConfig.guard_band_hz} Hz | masks ${masks.length} | failover ${buildConfig.backup_failover_holdoff_ms} ms`,
        `Runtime: thresholds ${buildConfig.rx_thresh_enable === 1 ? "enabled" : "disabled"} | RSSI ${buildConfig.rx_min_rssi_dbm} dBm | LQI ${buildConfig.rx_min_lqi} | poll ${buildConfig.rx_poll_interval_ms} ms | TX timeout ${buildConfig.tx_complete_timeout_ms} ms | bridge ${buildConfig.rx_host_bridge_budget} | GPS ${buildConfig.telem_gps_period_ms} ms | IMU/Baro ${buildConfig.telem_imu_baro_period_ms} ms | airtime ${buildConfig.airtime_limit_us_per_hour} us/hour`,
      ].join("\n");
      writeState();
    };

    const validateIntegerInput = (input, label, range) => {
      const parsed = parseInteger(input.value);
      if (parsed == null) {
        throw new Error(`${label} is required and must be an integer.`);
      }
      const min =
        Number.isInteger(range?.min)
          ? range.min
          : "min" in input && input.min !== ""
            ? Number(input.min)
            : null;
      const max =
        Number.isInteger(range?.max)
          ? range.max
          : "max" in input && input.max !== ""
            ? Number(input.max)
            : null;
      if (Number.isInteger(min) && parsed < min) {
        throw new Error(`${label} must be at least ${min}.`);
      }
      if (Number.isInteger(max) && parsed > max) {
        throw new Error(`${label} must be at most ${max}.`);
      }
      return parsed;
    };

    const validateOptionalIntegerInput = (input, label, range) => {
      if (input.value.trim() === "") {
        return null;
      }
      return validateIntegerInput(input, label, range);
    };

    const serializeBuildConfig = () => {
      if (!loadedBuildConfig) {
        throw new Error("Load config for this SHA first.");
      }

      const primaryChannel = validateIntegerInput(allowlist0, "Primary Channel", null);
      const backupChannel = validateOptionalIntegerInput(allowlist1, "Backup Channel", null);
      const allowlist = [allowlist0, allowlist1]
        .map((_input, index) => (index === 0 ? primaryChannel : backupChannel))
        .filter((value) => value != null);
      const allowlistMin = loadedBuildConstraints?.allowlist_hz_length?.min ?? 1;
      const allowlistMax = loadedBuildConstraints?.allowlist_hz_length?.max ?? 2;
      if (allowlist.length < allowlistMin || allowlist.length > allowlistMax) {
        throw new Error(`Allowlist must contain between ${allowlistMin} and ${allowlistMax} channels.`);
      }

      const exclusionMasks = [];
      for (const [index, row] of exclusionMaskInputs.entries()) {
        const center = validateOptionalIntegerInput(
          row.center,
          `Mask ${index + 1} Center`,
          loadedBuildConstraints?.exclusion_mask_center_hz
        );
        const halfBw = validateOptionalIntegerInput(
          row.halfBw,
          `Mask ${index + 1} Half BW`,
          loadedBuildConstraints?.exclusion_mask_half_bw_hz
        );
        if (center == null && halfBw == null) {
          continue;
        }
        if (center == null || halfBw == null) {
          throw new Error(`Mask ${index + 1} must include both center and half bandwidth values.`);
        }
        exclusionMasks.push({ center_hz: center, half_bw_hz: halfBw });
      }

      const exclusionMax = loadedBuildConstraints?.exclusion_masks_length?.max ?? 4;
      if (exclusionMasks.length > exclusionMax) {
        throw new Error(`Exclusion masks cannot exceed ${exclusionMax} entries.`);
      }

      const bandMinValue = validateIntegerInput(bandMinHz, "Band Min", loadedBuildConstraints?.band_min_hz);
      const bandMaxValue = validateIntegerInput(bandMaxHz, "Band Max", loadedBuildConstraints?.band_max_hz);
      if (bandMinValue >= bandMaxValue) {
        throw new Error("Band Min must be less than Band Max.");
      }

      return {
        machine_log_detail: forcedMachineLogDetail,
        machine_log_stat_period_ms: loadedBuildConfig.machine_log_stat_period_ms,
        rf_bitrate_bps: validateIntegerInput(
          rfBitrateBps,
          "Bitrate",
          loadedBuildConstraints?.rf_bitrate_bps
        ),
        rf_rx_bw_hz: validateIntegerInput(
          rfRxBwHz,
          "RX Bandwidth",
          loadedBuildConstraints?.rf_rx_bw_hz
        ),
        rf_deviation_hz: validateIntegerInput(
          rfDeviationHz,
          "Deviation",
          loadedBuildConstraints?.rf_deviation_hz
        ),
        rf_preamble_bytes: validateIntegerInput(rfPreambleBytes, "Preamble", null),
        rf_sync_word: validateIntegerInput(rfSyncWord, "Sync Word", loadedBuildConstraints?.rf_sync_word),
        rf_pa_table: paTableInputs.map((input, index) =>
          validateIntegerInput(input, `PA ${index + 1}`, loadedBuildConstraints?.rf_pa_table_value)
        ),
        allowlist_hz: allowlist,
        band_min_hz: bandMinValue,
        band_max_hz: bandMaxValue,
        guard_band_hz: validateIntegerInput(
          guardBandHz,
          "Guard Band",
          loadedBuildConstraints?.guard_band_hz
        ),
        exclusion_masks: exclusionMasks,
        backup_failover_holdoff_ms: validateIntegerInput(
          backupFailoverHoldoffMs,
          "Failover Holdoff",
          loadedBuildConstraints?.backup_failover_holdoff_ms
        ),
        rx_thresh_enable: validateIntegerInput(
          rxThreshEnable,
          "RX Thresholding",
          null
        ),
        rx_min_rssi_dbm: validateIntegerInput(
          rxMinRssiDbm,
          "RX Min RSSI",
          loadedBuildConstraints?.rx_min_rssi_dbm
        ),
        rx_min_lqi: validateIntegerInput(rxMinLqi, "RX Min LQI", loadedBuildConstraints?.rx_min_lqi),
        rx_thresh_log_every: validateIntegerInput(
          rxThreshLogEvery,
          "Threshold Log Every",
          loadedBuildConstraints?.rx_thresh_log_every
        ),
        rx_poll_interval_ms: validateIntegerInput(
          rxPollIntervalMs,
          "RX Poll Interval",
          loadedBuildConstraints?.rx_poll_interval_ms
        ),
        tx_complete_timeout_ms: validateIntegerInput(
          txCompleteTimeoutMs,
          "TX Complete Timeout",
          loadedBuildConstraints?.tx_complete_timeout_ms
        ),
        rx_host_bridge_budget: validateIntegerInput(
          rxHostBridgeBudget,
          "RX Host Bridge Budget",
          loadedBuildConstraints?.rx_host_bridge_budget
        ),
        telem_gps_period_ms: validateIntegerInput(
          telemGpsPeriodMs,
          "Telemetry GPS Period",
          loadedBuildConstraints?.telem_gps_period_ms
        ),
        telem_imu_baro_period_ms: validateIntegerInput(
          telemImuBaroPeriodMs,
          "Telemetry IMU/Baro Period",
          loadedBuildConstraints?.telem_imu_baro_period_ms
        ),
        airtime_limit_us_per_hour: validateIntegerInput(
          airtimeLimitUsPerHour,
          "Airtime Limit",
          loadedBuildConstraints?.airtime_limit_us_per_hour
        ),
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

    const populateConfigForm = (payload) => {
      loadedBuildConfig = normalizeBuildConfigForUi(payload.build_config);
      loadedBuildConstraints = payload.constraints;
      applyConstraints(payload.constraints);
      applyBuildConfigToForm(loadedBuildConfig);
      renderSummary(loadedBuildConfig, payload.git_sha);
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
        setStatus(`Loaded ${payload.git_sha}. Repo defaults are now in the form and logging remains locked by RTMS.`);
      } catch (error) {
        loadedConfigSha = "";
        loadedBuildConfig = null;
        loadedBuildConstraints = null;
        clearConfigForm();
        summaryField.textContent = emptySummaryText;
        setStatus(error.message, true);
      }
      writeState();
    };

    const restoreState = () => {
      const state = readState();
      if (!state) {
        clearConfigForm();
        updateBuildTargetUi();
        return;
      }
      repoSelect.value = state.repoId || repoSelect.value;
      commitQuery.value = state.commitQuery || "";
      gitShaInput.value = state.gitSha || "";
      buildRoleSelect.value = state.role || buildRoleSelect.value;
      const buildHostSelect = form.elements.namedItem("build_host_id");
      if (buildHostSelect instanceof HTMLSelectElement && state.buildHostId) {
        buildHostSelect.value = state.buildHostId;
      }
      loadedConfigSha = state.loadedConfigSha || "";
      loadedBuildConfig = state.loadedBuildConfig || null;
      loadedBuildConstraints = state.loadedBuildConstraints || null;
      if (loadedBuildConstraints) {
        applyConstraints(loadedBuildConstraints);
      }
      if (state.fieldValues) {
        applyFieldValues(state.fieldValues);
      } else if (loadedBuildConfig) {
        applyBuildConfigToForm(loadedBuildConfig);
      } else {
        clearConfigForm();
      }
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
        clearConfigForm();
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

    for (const input of configInputs) {
      if (input.dataset.buildBound === "true") {
        continue;
      }
      input.dataset.buildBound = "true";
      input.addEventListener("input", updateSummaryPreview);
      input.addEventListener("change", writeState);
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
