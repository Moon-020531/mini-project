package com.posturedesk.dto;

import lombok.Getter;
import lombok.Setter;

@Getter
@Setter
public class PostureLogRequest {
    private Integer goodPostureTime;
    private Integer warningCount;
    private String username;
}
