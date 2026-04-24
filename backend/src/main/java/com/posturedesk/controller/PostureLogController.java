package com.posturedesk.controller;

import com.posturedesk.dto.PostureLogRequest;
import com.posturedesk.entity.PostureLog;
import com.posturedesk.repository.PostureLogRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
@RequestMapping("/api/log")
@CrossOrigin(origins = "*")
@RequiredArgsConstructor
public class PostureLogController {

    private final PostureLogRepository postureLogRepository;

    @PostMapping
    public ResponseEntity<String> saveLog(@RequestBody PostureLogRequest request) {
        PostureLog log = new PostureLog();
        log.setGoodPostureTime(request.getGoodPostureTime());
        log.setWarningCount(request.getWarningCount());
        log.setUsername(request.getUsername() == null ? "anonymous" : request.getUsername());
        postureLogRepository.save(log);
        return ResponseEntity.ok("Log saved successfully");
    }

    @GetMapping
    public ResponseEntity<List<PostureLog>> getAllLogs(@RequestParam(required = false) String username) {
        List<PostureLog> logs = postureLogRepository.findAll();
        if (username != null && !username.isEmpty()) {
            logs = logs.stream().filter(l -> username.equals(l.getUsername())).toList();
        }
        return ResponseEntity.ok(logs);
    }
}
