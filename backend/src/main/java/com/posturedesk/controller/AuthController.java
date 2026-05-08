package com.posturedesk.controller;

import com.posturedesk.dto.AuthRequest;
import com.posturedesk.dto.GoogleLoginRequest;
import com.posturedesk.entity.User;
import com.posturedesk.repository.UserRepository;
import com.google.api.client.googleapis.auth.oauth2.GoogleIdToken;
import com.google.api.client.googleapis.auth.oauth2.GoogleIdTokenVerifier;
import com.google.api.client.http.javanet.NetHttpTransport;
import com.google.api.client.json.gson.GsonFactory;
import lombok.RequiredArgsConstructor;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.*;

@RestController
@RequestMapping("/api/auth")
@CrossOrigin(origins = "*")
@RequiredArgsConstructor
public class AuthController {

    private final UserRepository userRepository;

    @Value("${google.client.id}")
    private String googleClientId;

    // ========== 기존 로그인/회원가입 (유지) ==========

    // 간단한 해커톤용 목업 토큰 발급 로직
    @PostMapping("/signup")
    public ResponseEntity<?> signup(@RequestBody AuthRequest request) {
        if (userRepository.findByUsername(request.getUsername()).isPresent()) {
            return ResponseEntity.status(HttpStatus.BAD_REQUEST).body("이미 존재하는 아이디입니다.");
        }

        User user = new User();
        user.setUsername(request.getUsername());
        // 실제 운영에선 BCrypt 등 해시 암호화를 써야하지만, 해커톤 특성상 평문 저장
        user.setPassword(request.getPassword());
        user.setProvider("LOCAL");
        userRepository.save(user);

        return ResponseEntity.ok("회원가입 성공");
    }

    @PostMapping("/login")
    public ResponseEntity<?> login(@RequestBody AuthRequest request) {
        Optional<User> userOpt = userRepository.findByUsername(request.getUsername());

        if (userOpt.isPresent() && userOpt.get().getPassword().equals(request.getPassword())) {
            // 임시 토큰 발급 (실제로는 JWT 권장)
            String token = UUID.randomUUID().toString();
            Map<String, Object> response = new HashMap<>();
            response.put("token", token);
            response.put("username", userOpt.get().getUsername());
            response.put("profileImage", userOpt.get().getProfileImage());
            return ResponseEntity.ok(response);
        }

        return ResponseEntity.status(HttpStatus.UNAUTHORIZED).body("아이디 또는 비밀번호가 틀렸습니다.");
    }

    // ========== Google OAuth 로그인 (신규) ==========

    @PostMapping("/google")
    public ResponseEntity<?> googleLogin(@RequestBody GoogleLoginRequest request) {
        try {
            // Google ID Token 검증
            GoogleIdTokenVerifier verifier = new GoogleIdTokenVerifier.Builder(
                    new NetHttpTransport(), GsonFactory.getDefaultInstance())
                    .setAudience(Collections.singletonList(googleClientId))
                    .build();

            GoogleIdToken idToken = verifier.verify(request.getCredential());

            if (idToken == null) {
                return ResponseEntity.status(HttpStatus.UNAUTHORIZED).body("유효하지 않은 Google 토큰입니다.");
            }

            GoogleIdToken.Payload payload = idToken.getPayload();
            String email = payload.getEmail();
            String name = (String) payload.get("name");
            String picture = (String) payload.get("picture");

            // DB에서 이메일로 기존 유저 조회, 없으면 자동 생성
            User user = userRepository.findByEmail(email).orElseGet(() -> {
                User newUser = new User();
                newUser.setEmail(email);
                // username은 이메일의 @ 앞부분 사용 (중복 시 랜덤 접미사 추가)
                String baseUsername = email.split("@")[0];
                String username = baseUsername;
                int suffix = 1;
                while (userRepository.findByUsername(username).isPresent()) {
                    username = baseUsername + suffix++;
                }
                newUser.setUsername(username);
                newUser.setProvider("GOOGLE");
                newUser.setProfileImage(picture);
                return userRepository.save(newUser);
            });

            // 기존 유저라면 프로필 이미지 업데이트
            if (user.getProfileImage() == null || !user.getProfileImage().equals(picture)) {
                user.setProfileImage(picture);
                userRepository.save(user);
            }

            // 토큰 발급
            String token = UUID.randomUUID().toString();
            Map<String, Object> response = new HashMap<>();
            response.put("token", token);
            response.put("username", user.getUsername());
            response.put("profileImage", user.getProfileImage());

            return ResponseEntity.ok(response);

        } catch (Exception e) {
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                    .body("Google 로그인 처리 중 오류: " + e.getMessage());
        }
    }

    // ========== Naver OAuth 로그인 (신규) ==========

    @Value("${naver.client.id}")
    private String naverClientId;

    @Value("${naver.client.secret}")
    private String naverClientSecret;

    @PostMapping("/naver")
    public ResponseEntity<?> naverLogin(@RequestBody com.posturedesk.dto.NaverLoginRequest request) {
        try {
            org.springframework.web.client.RestTemplate restTemplate = new org.springframework.web.client.RestTemplate();

            // 1. 네이버 토큰 발급 요청 (안전한 URI 빌드)
            String tokenUrl = org.springframework.web.util.UriComponentsBuilder.fromHttpUrl("https://nid.naver.com/oauth2.0/token")
                    .queryParam("grant_type", "authorization_code")
                    .queryParam("client_id", naverClientId)
                    .queryParam("client_secret", naverClientSecret)
                    .queryParam("code", request.getCode())
                    .queryParam("state", request.getState())
                    .toUriString();

            ResponseEntity<Map> tokenResponse;
            try {
                tokenResponse = restTemplate.getForEntity(tokenUrl, Map.class);
            } catch (Exception e) {
                System.err.println("네이버 토큰 발급 에러: " + e.getMessage());
                return ResponseEntity.status(HttpStatus.UNAUTHORIZED).body("네이버 토큰 발급 실패: " + e.getMessage());
            }

            if (!tokenResponse.getStatusCode().is2xxSuccessful() || tokenResponse.getBody() == null) {
                return ResponseEntity.status(HttpStatus.UNAUTHORIZED).body("네이버 토큰 발급 응답 오류");
            }
            
            String accessToken = (String) tokenResponse.getBody().get("access_token");
            if (accessToken == null) {
                System.err.println("네이버 토큰 응답: " + tokenResponse.getBody());
                return ResponseEntity.status(HttpStatus.UNAUTHORIZED).body("네이버 액세스 토큰이 없습니다.");
            }

            // 2. 네이버 유저 프로필 조회
            org.springframework.http.HttpHeaders headers = new org.springframework.http.HttpHeaders();
            headers.setBearerAuth(accessToken);
            org.springframework.http.HttpEntity<String> entity = new org.springframework.http.HttpEntity<>("", headers);

            ResponseEntity<Map> profileResponse;
            try {
                profileResponse = restTemplate.exchange(
                        "https://openapi.naver.com/v1/nid/me",
                        org.springframework.http.HttpMethod.GET,
                        entity,
                        Map.class
                );
            } catch (Exception e) {
                System.err.println("네이버 프로필 조회 에러 (토큰: " + accessToken + "): " + e.getMessage());
                return ResponseEntity.status(HttpStatus.UNAUTHORIZED).body("네이버 프로필 조회 실패: " + e.getMessage());
            }

            if (!profileResponse.getStatusCode().is2xxSuccessful() || profileResponse.getBody() == null) {
                return ResponseEntity.status(HttpStatus.UNAUTHORIZED).body("네이버 프로필 조회 실패");
            }

            Map<String, Object> responseBody = (Map<String, Object>) profileResponse.getBody().get("response");
            String email = (String) responseBody.get("email");
            String picture = (String) responseBody.get("profile_image");
            
            if (email == null) {
                return ResponseEntity.status(HttpStatus.BAD_REQUEST).body("이메일 정보 제공 동의가 필요합니다.");
            }

            // 3. DB에서 이메일로 기존 유저 조회, 없으면 자동 생성
            User user = userRepository.findByEmail(email).orElseGet(() -> {
                User newUser = new User();
                newUser.setEmail(email);
                
                String baseUsername = email.split("@")[0] + "_n"; // 네이버 유저 구분용 접미사
                String username = baseUsername;
                int suffix = 1;
                while (userRepository.findByUsername(username).isPresent()) {
                    username = baseUsername + suffix++;
                }
                newUser.setUsername(username);
                newUser.setProvider("NAVER");
                newUser.setProfileImage(picture);
                return userRepository.save(newUser);
            });

            // 기존 유저라면 프로필 이미지 업데이트
            if (user.getProfileImage() == null || !user.getProfileImage().equals(picture)) {
                user.setProfileImage(picture);
                userRepository.save(user);
            }

            // 4. 서비스 토큰 발급
            String serviceToken = UUID.randomUUID().toString();
            Map<String, Object> finalResponse = new HashMap<>();
            finalResponse.put("token", serviceToken);
            finalResponse.put("username", user.getUsername());
            finalResponse.put("profileImage", user.getProfileImage());

            return ResponseEntity.ok(finalResponse);

        } catch (Exception e) {
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                    .body("네이버 로그인 처리 중 오류: " + e.getMessage());
        }
    }
}
