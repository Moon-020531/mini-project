CREATE DATABASE IF NOT EXISTS turtle_neck_db;
USE turtle_neck_db;

CREATE TABLE IF NOT EXISTS posture_log (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    record_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '기록 시간',
    good_posture_time INT NOT NULL COMMENT '바른 자세 유지 시간(초)',
    warning_count INT NOT NULL COMMENT '거북목 경고 횟수'
);
