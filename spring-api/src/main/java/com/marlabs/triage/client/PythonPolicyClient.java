package com.marlabs.triage.client;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.marlabs.triage.model.Caller;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.*;
import org.springframework.stereotype.Component;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.util.MultiValueMap;
import org.springframework.core.io.ByteArrayResource;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.RestTemplate;
import org.springframework.web.client.HttpStatusCodeException;
import java.util.Map;

@Component
public class PythonPolicyClient {
	private final RestTemplate restTemplate;
	private final ObjectMapper mapper;
	private final String baseUrl;

	public PythonPolicyClient(ObjectMapper mapper,
			@Value("${python-service.base-url:http://localhost:8000}") String baseUrl,
			@Value("${python-service.connect-timeout-ms:1000}") int connectTimeout,
			@Value("${python-service.read-timeout-ms:5000}") int readTimeout) {
		this.mapper = mapper;
		this.baseUrl = baseUrl;
		SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
		factory.setConnectTimeout(connectTimeout);
		factory.setReadTimeout(readTimeout);
		this.restTemplate = new RestTemplate(factory);
	}

	public JsonNode answer(String question, String asOf, Caller caller) {
		
		try {
			return restTemplate.postForObject(baseUrl + "/internal/answer",
					Map.of("question", question, "as_of", asOf, "tenant", caller.tenant(), "role", caller.role()),
					JsonNode.class);
		} catch (RestClientException ex) {
			throw new DependencyFailure("POLICY_PROVIDER_FAILURE", "Policy analysis service is unavailable.", ex);
		}
	}

	public JsonNode analyze(String filename, byte[] bytes, String asOf, Caller caller) {
		
		try {
			MultiValueMap<String, Object> body = new LinkedMultiValueMap<>();
			body.add("tenant", caller.tenant());
			body.add("role", caller.role());
			body.add("as_of", asOf);
			body.add("filename", filename);
			
			ByteArrayResource resource = new ByteArrayResource(bytes) {
				@Override
				public String getFilename() {
					return filename;
				}
			};
			
			HttpHeaders fileHeaders = new HttpHeaders();
			fileHeaders.setContentType(MediaType.APPLICATION_OCTET_STREAM);
			body.add("file", new HttpEntity<>(resource, fileHeaders));
			HttpHeaders headers = new HttpHeaders();
			headers.setContentType(MediaType.MULTIPART_FORM_DATA);
			
			return restTemplate.postForObject(baseUrl + "/internal/analyze", new HttpEntity<>(body, headers),
					JsonNode.class);
		} catch (HttpStatusCodeException ex) {
			
			try {
				JsonNode error = mapper.readTree(ex.getResponseBodyAsString());
				JsonNode detail = error.path("detail");
				throw new ItemFailure(detail.path("code").asText("DOCUMENT_ANALYSIS_FAILURE"),
						detail.path("message").asText("Document could not be processed."));
			} catch (ItemFailure known) {
				throw known;
			} catch (Exception parseFailure) {
				throw new ItemFailure("DOCUMENT_ANALYSIS_FAILURE", "Document could not be processed.");
			}
		} catch (RestClientException ex) {
			throw new DependencyFailure("DOCUMENT_ANALYSIS_FAILURE", "Document analysis service is unavailable.", ex);
		}
	}

	public static class DependencyFailure extends RuntimeException {
		private final String code;

		public DependencyFailure(String code, String message, Throwable cause) {
			super(message, cause);
			this.code = code;
		}

		public String code() {
			return code;
		}
	}

	public static class ItemFailure extends RuntimeException {
		private final String code;

		public ItemFailure(String code, String message) {
			super(message);
			this.code = code;
		}

		public String code() {
			return code;
		}
	}
}
