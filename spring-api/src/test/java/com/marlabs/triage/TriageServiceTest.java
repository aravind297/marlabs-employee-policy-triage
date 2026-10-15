package com.marlabs.triage;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.JsonNodeFactory;
import com.marlabs.triage.client.PythonPolicyClient;
import com.marlabs.triage.config.CallerRegistry;
import com.marlabs.triage.model.BatchManifest;
import com.marlabs.triage.service.TriageService;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockMultipartFile;
import java.util.List;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class TriageServiceTest {
    private final CallerRegistry registry = new CallerRegistry();
    private final PythonPolicyClient python = mock(PythonPolicyClient.class);
    private final ObjectMapper mapper = new ObjectMapper();
    private final TriageService service = new TriageService(registry, python, mapper);

    @Test void rejectsUnknownCaller() {
        TriageService.ApiException ex = assertThrows(TriageService.ApiException.class, () -> service.answer("unknown", "limit", "2026-09-21"));
        assertEquals("UNKNOWN_CALLER", ex.code());
    }
    @Test void rejectsInvalidCalendarDate() {
        assertThrows(TriageService.ApiException.class, () -> service.answer("atlas-employee-01", "limit", "2026-99-99"));
    }
    @Test void batchReturnsFailedResultForZeroByteItemAndContinues() throws Exception {
        String content = "Reference: CERT-101\nI request certification reimbursement of INR 18000.";
        MockMultipartFile one = new MockMultipartFile("files", "request-01.txt", "text/plain", content.getBytes());
        MockMultipartFile eight = new MockMultipartFile("files", "request-08.txt", "text/plain", new byte[0]);
        when(python.analyze(eq("request-01.txt"), any(byte[].class), eq("2026-09-21"), any()))
            .thenReturn(mapper.readTree("{\"extracted\":{\"benefit\":\"certification reimbursement\",\"amount\":\"18000\",\"currency\":\"INR\",\"reference\":\"CERT-101\"},\"field_evidence\":{},\"policy\":{\"status\":\"ANSWERED\",\"answer\":\"ok\",\"citations\":[]},\"review_required\":{\"required\":true,\"issues\":[]}}"));
        BatchManifest manifest = new BatchManifest("demo", "2026-09-21", List.of(new BatchManifest.DocumentEntry("request-01", "request-01.txt"), new BatchManifest.DocumentEntry("request-08", "request-08.txt")));
        var result = service.batch("atlas-employee-01", manifest, List.of(one, eight));
        assertEquals(2, result.path("summary").path("total").asInt());
        assertEquals(1, result.path("summary").path("completed").asInt());
        assertEquals(1, result.path("summary").path("failed").asInt());
        assertEquals("FAILED", result.path("results").get(1).path("processing_status").asText());
    }
    @Test void rejectsManifestFilenameMismatch() {
        MockMultipartFile file = new MockMultipartFile("files", "wrong.txt", "text/plain", "x".getBytes());
        BatchManifest manifest = new BatchManifest("demo", "2026-09-21", List.of(new BatchManifest.DocumentEntry("id", "expected.txt")));
        assertThrows(TriageService.ApiException.class, () -> service.batch("atlas-employee-01", manifest, List.of(file)));
    }
}
