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
}
