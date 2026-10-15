package com.marlabs.triage.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.marlabs.triage.client.PythonPolicyClient;
import com.marlabs.triage.config.CallerRegistry;
import com.marlabs.triage.model.BatchManifest;
import com.marlabs.triage.model.Caller;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;
import java.io.IOException;
import java.security.MessageDigest;
import java.time.LocalDate;
import java.time.format.DateTimeParseException;
import java.util.*;

@Service
public class TriageService {
	private static final Logger log = LoggerFactory.getLogger(TriageService.class);
	private final CallerRegistry callerRegistry;
	private final PythonPolicyClient python;
	private final ObjectMapper mapper;

	public TriageService(CallerRegistry callerRegistry, PythonPolicyClient python, ObjectMapper mapper) {
		this.callerRegistry = callerRegistry;
		this.python = python;
		this.mapper = mapper;
	}

	public Caller requireCaller(String callerId) {
		
		if (callerId == null || callerId.isBlank())
			throw new ApiException(400, "CALLER_REQUIRED", "X-Caller-Id header is required.");
		
		return callerRegistry.find(callerId)
				.orElseThrow(() -> new ApiException(401, "UNKNOWN_CALLER", "Caller identity is not recognized."));
	}

	public LocalDate parseDate(String value) {
		
		try {
			return LocalDate.parse(value);
		} catch (DateTimeParseException | NullPointerException ex) {
			throw new ApiException(400, "INVALID_AS_OF", "as_of must be a valid date in YYYY-MM-DD format.");
		}
	}

	public JsonNode answer(String callerId, String question, String asOf) {
		
		Caller caller = requireCaller(callerId);
		parseDate(asOf);
		
		if (question == null || question.isBlank())
			throw new ApiException(400, "INVALID_QUESTION", "question must be a non-empty string.");
		
		return python.answer(question, asOf, caller);
	}

	public JsonNode batch(String callerId, BatchManifest manifest, List<MultipartFile> files) {
		
		Caller caller = requireCaller(callerId);
		
		if (manifest == null)
			throw new ApiException(400, "INVALID_METADATA", "Metadata JSON part is required.");
		parseDate(manifest.as_of());
		
		if (manifest.batch_id() == null || manifest.batch_id().isBlank() || manifest.documents() == null
				|| manifest.documents().isEmpty())
			throw new ApiException(400, "INVALID_METADATA", "batch_id and at least one document entry are required.");

		Set<String> ids = new HashSet<>(), names = new HashSet<>();
		
		for (BatchManifest.DocumentEntry entry : manifest.documents()) {
			
			if (entry == null || entry.document_id() == null || entry.document_id().isBlank()
					|| entry.filename() == null || entry.filename().isBlank())
				throw new ApiException(400, "INVALID_METADATA",
						"Each manifest entry requires document_id and filename.");
			
			if (!ids.add(entry.document_id()))
				throw new ApiException(400, "DUPLICATE_DOCUMENT_ID", "document_id values must be unique.");
			
			if (!names.add(entry.filename()))
				throw new ApiException(400, "DUPLICATE_FILENAME", "filename values must be unique.");
		}

		if (files == null || files.size() != manifest.documents().size())
			throw new ApiException(400, "FILE_PART_COUNT_MISMATCH", "File part count must match the manifest.");
		Map<String, MultipartFile> byName = new HashMap<>();
		
		for (MultipartFile file : files) {
			if (file == null || file.getOriginalFilename() == null || file.getOriginalFilename().isBlank()
					|| byName.put(file.getOriginalFilename(), file) != null)
				throw new ApiException(400, "INVALID_FILE_PARTS",
						"Every file part must have a unique original filename.");
		}
		
		if (!byName.keySet().equals(names))
			throw new ApiException(400, "FILE_MANIFEST_MISMATCH",
					"Uploaded filenames must match manifest filenames exactly.");

		ArrayNode results = mapper.createArrayNode();
		
		Map<String, String> firstIdByHash = new HashMap<>();
		int completed = 0, failed = 0;
		
		for (BatchManifest.DocumentEntry entry : manifest.documents()) {
			ObjectNode result = mapper.createObjectNode();
			result.put("document_id", entry.document_id());
			String duplicateOf = null;
			
			try {
				byte[] bytes = byName.get(entry.filename()).getBytes();
				String hash = sha256(bytes);
				duplicateOf = firstIdByHash.putIfAbsent(hash, entry.document_id());
				
				if (bytes.length == 0)
					throw new PythonPolicyClient.ItemFailure("EMPTY_FILE", "The uploaded file is empty.");
				JsonNode analyzed = python.analyze(entry.filename(), bytes, manifest.as_of(), caller);
				result.put("processing_status", "COMPLETED");
				result.set("extracted", analyzed.path("extracted").deepCopy());
				result.set("field_evidence", analyzed.path("field_evidence").deepCopy());
				JsonNode policy = analyzed.path("policy");
				result.set("policy", policy.isMissingNode() || policy.isNull() ? mapper.getNodeFactory().nullNode()
						: policy.deepCopy());
				result.set("review_required", analyzed.path("review_required").deepCopy());
				
				if (duplicateOf == null)
					result.putNull("duplicate_of");

				else
					result.put("duplicate_of", duplicateOf);
				result.putNull("error");
				completed++;
				log.info("batch_item_complete batch_id={} document_id={} duplicate_of={}", manifest.batch_id(),
						entry.document_id(), duplicateOf);
			} catch (PythonPolicyClient.ItemFailure ex) {
				failed++;
				result.put("processing_status", "FAILED");
				result.putNull("extracted");
				result.putNull("field_evidence");
				result.putNull("policy");
				ObjectNode review = mapper.createObjectNode();
				review.put("required", true);
				review.putArray("issues").add("PROCESSING_FAILURE: " + ex.getMessage());
				result.set("review_required", review);
				
				if (duplicateOf == null)
					result.putNull("duplicate_of");
				else
					result.put("duplicate_of", duplicateOf);
				
				ObjectNode error = mapper.createObjectNode();
				error.put("code", ex.code());
				error.put("message", ex.getMessage());
				result.set("error", error);
				log.warn("batch_item_failed batch_id={} document_id={} code={}", manifest.batch_id(),
						entry.document_id(), ex.code());
			} catch (PythonPolicyClient.DependencyFailure ex) {
				failed++;
				result.put("processing_status", "FAILED");
				result.putNull("extracted");
				result.putNull("field_evidence");
				result.putNull("policy");
				ObjectNode review = mapper.createObjectNode();
				review.put("required", true);
				review.putArray("issues").add("PROCESSING_FAILURE: Document analysis dependency failed.");
				result.set("review_required", review);
				
				if (duplicateOf == null)
					result.putNull("duplicate_of");
				else
					result.put("duplicate_of", duplicateOf);
				
				ObjectNode error = mapper.createObjectNode();
				error.put("code", ex.code());
				error.put("message", ex.getMessage());
				result.set("error", error);
				log.warn("batch_item_dependency_failed batch_id={} document_id={} code={}", manifest.batch_id(),
						entry.document_id(), ex.code());
			} catch (IOException ex) {
				failed++;
				result.put("processing_status", "FAILED");
				result.putNull("extracted");
				result.putNull("field_evidence");
				result.putNull("policy");
				ObjectNode review = mapper.createObjectNode();
				review.put("required", true);
				review.putArray("issues").add("PROCESSING_FAILURE: Uploaded file could not be read.");
				result.set("review_required", review);
				
				if (duplicateOf == null)
					result.putNull("duplicate_of");
				else
					result.put("duplicate_of", duplicateOf);
				
				ObjectNode error = mapper.createObjectNode();
				error.put("code", "FILE_READ_FAILURE");
				error.put("message", "Uploaded file could not be read.");
				result.set("error", error);
				log.warn("batch_item_file_read_failed batch_id={} document_id={}", manifest.batch_id(),
						entry.document_id());
			}
			results.add(result);
		}
		ObjectNode response = mapper.createObjectNode();
		response.put("batch_id", manifest.batch_id());
		ObjectNode summary = response.putObject("summary");
		summary.put("total", manifest.documents().size());
		summary.put("completed", completed);
		summary.put("failed", failed);
		response.set("results", results);
		return response;
	}

	private String sha256(byte[] bytes) {
		
		try {
			return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
		} catch (Exception ex) {
			throw new IllegalStateException("SHA-256 is required by the runtime", ex);
		}
	}

	public static class ApiException extends RuntimeException {
		private final int status;
		private final String code;

		public ApiException(int status, String code, String message) {
			super(message);
			this.status = status;
			this.code = code;
		}

		public int status() {
			return status;
		}

		public String code() {
			return code;
		}
	}
}
