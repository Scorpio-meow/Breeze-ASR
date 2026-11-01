from __future__ import annotations

import logging

from openvino_telemetry.utils.opt_in_checker import ConsentCheckResult, OptInChecker

log = logging.getLogger(__name__)


def disable_openvino_telemetry() -> None:
    checker = OptInChecker()
    if checker.check(enable_opt_in_dialog=False, disable_in_ci=True) == ConsentCheckResult.DECLINED:
        return
    if not checker.create_or_check_consent_dir() or not checker.update_result(ConsentCheckResult.DECLINED):
        log.warning("無法寫入 %s，OpenVINO 與 NNCF 仍會送出用量回報", checker.consent_file())
        return
    log.info("已關閉 OpenVINO 與 NNCF 的用量回報（%s）", checker.consent_file())
