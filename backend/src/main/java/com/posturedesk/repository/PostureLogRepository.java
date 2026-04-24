package com.posturedesk.repository;

import com.posturedesk.entity.PostureLog;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

@Repository
public interface PostureLogRepository extends JpaRepository<PostureLog, Long> {
}
