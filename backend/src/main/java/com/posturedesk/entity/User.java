package com.posturedesk.entity;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

@Entity
@Table(name = "user")
@Getter
@Setter
@NoArgsConstructor
public class User {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(nullable = false, unique = true)
    private String username;

    @Column(nullable = true)
    private String password;

    @Column(nullable = true, unique = true)
    private String email;

    // "LOCAL" 또는 "GOOGLE"
    @Column(nullable = false)
    private String provider = "LOCAL";

    @Column(nullable = true)
    private String profileImage;
}
