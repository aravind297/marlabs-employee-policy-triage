package com.marlabs.triage.api;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.marlabs.triage.model.AnswerRequest;
import com.marlabs.triage.model.BatchManifest;
import com.marlabs.triage.service.TriageService;
import jakarta.validation.Valid;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;
import java.util.List;

@RestController
public class TriageController {
	private final TriageService service;
	private final ObjectMapper mapper;

	public TriageController(TriageService service, ObjectMapper mapper) {
		this.service = service;
		this.mapper = mapper;
	}

	@PostMapping(value = "/answer", consumes = MediaType.APPLICATION_JSON_VALUE, produces = MediaType.APPLICATION_JSON_VALUE)
	public JsonNode answer(@RequestHeader(value = "X-Caller-Id", required = false) String callerId,
			@Valid @RequestBody AnswerRequest request) {
		return service.answer(callerId, request.question(), request.as_of());
	}

	@PostMapping(value = "/batches", consumes = MediaType.MULTIPART_FORM_DATA_VALUE, produces = MediaType.APPLICATION_JSON_VALUE)
	public JsonNode batch(@RequestHeader(value = "X-Caller-Id", required = false) String callerId,
			@RequestPart("metadata") String metadata, @RequestPart("files") List<MultipartFile> files) {
		
		try {
			return service.batch(callerId, mapper.readValue(metadata, BatchManifest.class), files);
		} catch (TriageService.ApiException ex) {
			throw ex;
		} catch (Exception ex) {
			throw new TriageService.ApiException(400, "INVALID_METADATA",
					"Metadata must be valid JSON matching the batch manifest contract.");
		}
	}
}
