package com.marlabs.triage.model;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;

public record AnswerRequest(@NotBlank String question,
		@NotBlank @Pattern(regexp = "\\d{4}-\\d{2}-\\d{2}") String as_of) {
}
