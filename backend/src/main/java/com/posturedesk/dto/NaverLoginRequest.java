package com.posturedesk.dto;

import lombok.Getter;
import lombok.Setter;

@Getter
@Setter
public class NaverLoginRequest {
    private String code;  // 네이버에서 발급한 인가 코드
    private String state; // CSRF 방지용 상태 토큰
}
