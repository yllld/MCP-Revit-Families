# -*- coding: utf-8 -*-
"""One real parameter/save/reopen test. No geometry, shared parameters or connectors."""
import hashlib
import io
import json
import math
import os
import traceback
import uuid

import clr
from System import DateTime, Double
from System.Diagnostics import Process
from pyrevit import DB, UI
from revit.revit2021.template_locator import find_template
from revit.revit2021.unit_converter import UnitConverter

PARAMETER_NAME = u"MCP_Ширина"
TYPE_NAME = "MCP_TEST_1000"
TOLERANCE_MM = 0.001


def _require(condition, message):
    if not condition:
        raise RuntimeError(message)


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _family_info(doc):
    _require(doc is not None and doc.IsFamilyDocument, "Expected a FamilyDocument")
    _require(doc.FamilyManager is not None, "FamilyManager is missing")
    owner = doc.OwnerFamily
    _require(owner is not None and owner.FamilyCategory is not None,
             "OwnerFamily or FamilyCategory is missing")
    return {"is_family_document": bool(doc.IsFamilyDocument), "title": doc.Title,
            "owner_family": owner.Name, "category": owner.FamilyCategory.Name,
            "category_id": owner.FamilyCategory.Id.IntegerValue}


def _read_parameter(manager):
    parameters = [p for p in manager.Parameters if p.Definition.Name == PARAMETER_NAME]
    _require(len(parameters) == 1, "Expected exactly one MCP_Width parameter")
    parameter = parameters[0]
    _require(not parameter.IsInstance, "Parameter must be a type parameter")
    _require(parameter.Definition.ParameterType == DB.ParameterType.Length,
             "ParameterType must be Length")
    _require(parameter.Definition.ParameterGroup == DB.BuiltInParameterGroup.PG_GEOMETRY,
             "Parameter group must be PG_GEOMETRY")
    _require(parameter.StorageType == DB.StorageType.Double, "Expected Double storage")
    types = [item for item in manager.Types if item.Name == TYPE_NAME]
    _require(len(types) == 1, "Expected exactly one MCP_TEST_1000 type")
    value = types[0].AsDouble(parameter)
    _require(value is not None, "FamilyType.AsDouble returned null")
    internal = float(value)
    mm = float(UnitConverter.internal_to_mm(internal))
    _require(not math.isnan(mm) and not math.isinf(mm), "Non-finite parameter value")
    return mm, internal, {"name": parameter.Definition.Name,
                          "parameter_type": str(parameter.Definition.ParameterType),
                          "parameter_group": str(parameter.Definition.ParameterGroup),
                          "is_instance": bool(parameter.IsInstance)}


class TestFailurePreprocessor(DB.IFailuresPreprocessor):
    """Collect warnings and force rollback on errors instead of opening a modal."""
    def __init__(self, result):
        self.result = result

    def PreprocessFailures(self, accessor):
        has_errors = False
        for failure in accessor.GetFailureMessages():
            message = failure.GetDescriptionText()
            if failure.GetSeverity() == DB.FailureSeverity.Warning:
                self.result["warnings"].append(message)
                accessor.DeleteWarning(failure)
            else:
                has_errors = True
                self.result["errors"].append(message)
        return (DB.FailureProcessingResult.ProceedWithRollBack if has_errors
                else DB.FailureProcessingResult.Continue)


def test_family_parameter_roundtrip(uiapp, payload, project_root):
    """Must be invoked inside Routes ExternalEvent. Owns only its two documents."""
    result = {"success": False, "test": "family_parameter_roundtrip",
              "stage": "validate_request", "errors": [], "warnings": [],
              "saved": False, "closed": False, "reopened": False,
              "verified": False, "closed_after_readback": False,
              "transaction_committed": False, "transaction_rolled_back": False,
              "family_type": TYPE_NAME, "tolerance_mm": TOLERANCE_MM,
              "process_id": Process.GetCurrentProcess().Id,
              "started_at_utc": DateTime.UtcNow.ToString("o")}
    doc = transaction = None
    template_path = template_hash = None
    log_dir = os.path.join(project_root, ".runtime")
    run_dir = None
    try:
        _require(isinstance(payload, dict), "Expected JSON object")
        _require(set(payload) == set(("request_id", "expected_process_id", "write_value_mm")),
                 "Unexpected request fields")
        request_id = str(uuid.UUID(payload["request_id"]))
        _require(request_id == payload["request_id"], "Expected canonical UUID request_id")
        _require(payload["expected_process_id"] == result["process_id"], "Process ID mismatch")
        write_mm = float(payload["write_value_mm"])
        _require(write_mm == 1000.0, "This integration test only writes 1000 mm")
        result.update({"request_id": request_id, "write_value_mm": write_mm})

        result["stage"] = "validate_revit"
        app = uiapp.Application
        result["revit_version"] = str(app.VersionNumber)
        result["revit_build"] = str(app.VersionBuild)
        result["revit_api_version"] = str(clr.GetClrType(DB.Document).Assembly.GetName().Version)
        result["revit_apiui_version"] = str(clr.GetClrType(UI.UIApplication).Assembly.GetName().Version)
        _require(result["revit_version"] == "2021" and
                 result["revit_api_version"] == "21.0.0.0" and
                 result["revit_apiui_version"] == "21.0.0.0", "Only Revit 2021 is supported")

        result["stage"] = "reserve_output"
        output_root = os.path.join(project_root, "tests", "output")
        if not os.path.isdir(output_root):
            os.makedirs(output_root)
        # UUID directory is created exclusively. Repeated request IDs cannot overwrite files.
        run_dir = os.path.join(output_root, request_id)
        os.mkdir(run_dir)
        log_dir = run_dir
        output_path = os.path.join(run_dir, "MCP_Test_Parameter_Roundtrip.rfa")
        result["output_path"] = output_path

        result["stage"] = "find_template"
        selection = find_template(app.FamilyTemplatePath)
        result["template_search"] = selection
        template_path = selection["selected"]["path"]
        result["template_path"] = template_path
        _require(os.path.isfile(template_path), "Template file no longer exists")
        info = DB.BasicFileInfo.Extract(template_path)
        try:
            result["template_format"] = str(info.Format)
            _require(str(info.Format) == "2021", "Template must be saved in Revit 2021 format")
        finally:
            info.Dispose()
        template_hash = _sha256(template_path)
        result["template_sha256_before"] = template_hash
        if selection["selected"]["kind"] != "mechanical":
            result["warnings"].append("Mechanical template unavailable; using standard metric Generic Model")

        result["stage"] = "create_family_document"
        doc = app.NewFamilyDocument(template_path)
        result["family"] = _family_info(doc)
        if selection["selected"]["kind"] == "mechanical":
            _require(doc.OwnerFamily.FamilyCategory.Id.IntegerValue == int(DB.BuiltInCategory.OST_MechanicalEquipment),
                     "Selected template is not Mechanical Equipment")

        result["stage"] = "create_parameter_and_type"
        manager = doc.FamilyManager
        _require(not any(p.Definition.Name == PARAMETER_NAME for p in manager.Parameters),
                 "Test parameter already exists in template")
        _require(not any(t.Name == TYPE_NAME for t in manager.Types), "Test type already exists in template")
        transaction = DB.Transaction(doc, "MCP parameter roundtrip")
        _require(transaction.Start() == DB.TransactionStatus.Started, "Could not start transaction")
        failure_options = transaction.GetFailureHandlingOptions()
        failure_options.SetClearAfterRollback(True)
        failure_options.SetFailuresPreprocessor(TestFailurePreprocessor(result))
        transaction.SetFailureHandlingOptions(failure_options)
        parameter = manager.AddParameter(PARAMETER_NAME, DB.BuiltInParameterGroup.PG_GEOMETRY,
                                         DB.ParameterType.Length, False)
        family_type = manager.NewType(TYPE_NAME)
        manager.CurrentType = family_type

        result["stage"] = "write_value"
        internal = UnitConverter.mm_to_internal(write_mm)
        manager.Set(parameter, Double(internal))
        doc.Regenerate()
        before_mm, before_internal, parameter_info = _read_parameter(manager)
        result.update({"value_before_save_mm": before_mm, "write_internal_value": before_internal,
                       "parameter": parameter_info})
        _require(abs(before_mm - write_mm) <= TOLERANCE_MM, "Value mismatch before saving")
        result["stage"] = "commit_transaction"
        status = transaction.Commit()
        result["transaction_status"] = str(status)
        result["transaction_rolled_back"] = status == DB.TransactionStatus.RolledBack
        _require(status == DB.TransactionStatus.Committed, "Transaction did not commit")
        result["transaction_committed"] = True
        transaction.Dispose()
        transaction = None
        _require(not doc.IsModifiable, "Document still has an open transaction")

        result["stage"] = "save_family"
        options = DB.SaveAsOptions()
        try:
            options.OverwriteExistingFile = False
            options.MaximumBackups = 1
            doc.SaveAs(output_path, options)
        finally:
            options.Dispose()
        _require(os.path.isfile(output_path) and os.path.getsize(output_path) > 0,
                 "SaveAs did not create a nonempty RFA")
        result["saved"] = True

        result["stage"] = "close_created_document"
        _require(doc.Close(False), "Could not close created test document")
        doc = None
        result["closed"] = True

        result["stage"] = "reopen_family"
        doc = app.OpenDocumentFile(output_path)
        result["reopened_family"] = _family_info(doc)
        result["reopened"] = True
        result["stage"] = "verify_readback"
        read_mm, read_internal, read_parameter = _read_parameter(doc.FamilyManager)
        delta = abs(read_mm - write_mm)
        result.update({"readback_value_mm": read_mm, "readback_internal_value": read_internal,
                       "delta_mm": delta, "parameter": read_parameter})
        _require(delta <= TOLERANCE_MM, "Readback differs by more than 0.001 mm")
        _require(result["family"]["category_id"] == result["reopened_family"]["category_id"],
                 "Family category changed after reopening")
        result["verified"] = True
        result["stage"] = "close_reopened_document"
        _require(doc.Close(False), "Could not close reopened test document")
        doc = None
        result["closed_after_readback"] = True
        result["output_size_bytes"] = os.path.getsize(output_path)
        result["stage"] = "complete"
        result["success"] = not result["errors"]
    except Exception as error:
        result.update({"success": False, "error_type": type(error).__name__,
                       "error": unicode(error), "traceback": traceback.format_exc()})
        result["errors"].append(unicode(error))
    finally:
        if transaction is not None:
            try:
                if transaction.GetStatus() == DB.TransactionStatus.Started:
                    result["transaction_rolled_back"] = transaction.RollBack() == DB.TransactionStatus.RolledBack
                transaction.Dispose()
            except Exception as error:
                result["success"] = False
                result["errors"].append("Transaction cleanup: " + unicode(error))
                result["cleanup_traceback"] = traceback.format_exc()
        if doc is not None:
            try:
                if doc.IsValidObject:
                    _require(doc.Close(False), "Cleanup could not close owned document")
                result["cleanup_document_closed"] = True
            except Exception as error:
                result["success"] = False
                result["errors"].append("Document cleanup: " + unicode(error))
                result["cleanup_traceback"] = result.get("cleanup_traceback", "") + traceback.format_exc()
        if template_hash:
            try:
                result["template_sha256_after"] = _sha256(template_path)
                result["template_unchanged"] = result["template_sha256_after"] == template_hash
                _require(result["template_unchanged"], "Source template changed during test")
            except Exception as error:
                result["success"] = False
                result["errors"].append(unicode(error))
                result["cleanup_traceback"] = result.get("cleanup_traceback", "") + traceback.format_exc()
        result["finished_at_utc"] = DateTime.UtcNow.ToString("o")
        try:
            if not os.path.isdir(log_dir):
                os.makedirs(log_dir)
            # Unique fallback name even for malformed / duplicate requests.
            log_path = os.path.join(log_dir, "roundtrip-{0}.log".format(uuid.uuid4()))
            result["log_path"] = log_path
            with io.open(log_path, "w", encoding="utf-8") as stream:
                stream.write(unicode(json.dumps(result, ensure_ascii=False, indent=2)))
        except Exception as error:
            result["success"] = False
            result["errors"].append("Could not write diagnostic log: " + unicode(error))
    return result
