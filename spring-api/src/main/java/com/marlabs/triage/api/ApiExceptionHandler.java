package com.marlabs.triage.api;

import com.marlabs.triage.client.PythonPolicyClient;
import com.marlabs.triage.service.TriageService;
import jakarta.validation.ConstraintViolationException;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import java.util.Map;

@RestControllerAdvice
public class ApiExceptionHandler {
	@ExceptionHandler(TriageService.ApiException.class)
	public ResponseEntity<?> api(TriageService.ApiException ex) {
		return ResponseEntity.status(ex.status())
				.body(Map.of("error", Map.of("code", ex.code(), "message", ex.getMessage())));
	}

	@ExceptionHandler(PythonPolicyClient.DependencyFailure.class)
	public ResponseEntity<?> dependency(PythonPolicyClient.DependencyFailure ex) {
		return ResponseEntity.status(502).body(Map.of("error", Map.of("code", ex.code(), "message", ex.getMessage())));
	}

	@ExceptionHandler({ MethodArgumentNotValidException.class, HttpMessageNotReadableException.class,
			ConstraintViolationException.class })
	public ResponseEntity<?> invalid(Exception ex) {
		return ResponseEntity.badRequest().body(
				Map.of("error", Map.of("code", "INVALID_REQUEST", "message", "Request body or date is invalid.")));
	}

	@ExceptionHandler(Exception.class)
	public ResponseEntity<?> unexpected(Exception ex) {
		return ResponseEntity.status(500).body(
				Map.of("error", Map.of("code", "INTERNAL_ERROR", "message", "The request could not be completed.")));
	}
}
