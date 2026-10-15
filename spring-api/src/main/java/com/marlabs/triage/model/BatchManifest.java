package com.marlabs.triage.model;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.Pattern;
import java.util.List;

public record BatchManifest(@NotBlank String batch_id,
                            @NotBlank @Pattern(regexp = "\\d{4}-\\d{2}-\\d{2}") String as_of,
                            @NotEmpty List<@Valid DocumentEntry> documents) {
    public record DocumentEntry(@NotBlank String document_id, @NotBlank String filename) { }
}
