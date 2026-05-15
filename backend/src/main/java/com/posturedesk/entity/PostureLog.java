package com.posturedesk.entity;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.time.LocalDateTime;

@Entity
@Table(name = "posture_log")
@Getter
@Setter
@NoArgsConstructor
public class PostureLog {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "record_time", nullable = false, updatable = false)
    private LocalDateTime recordTime;

    @Column(name = "good_posture_time", nullable = false)
    private Integer goodPostureTime;

    @Column(name = "warning_count", nullable = false)
    private Integer warningCount;

    @Column(name = "posture_score")
    private Integer postureScore;

    @Column(nullable = false)
    private String username;

    @PrePersist
    protected void onCreate() {
        if (recordTime == null) {
            recordTime = LocalDateTime.now();
        }
    }
}
